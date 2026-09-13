"""Сбор объявлений с Property Finder (Дубай).

Данные лежат прямо в странице, в блоке __NEXT_DATA__ — браузер не нужен.
Работает по страницам башни или района, например:
  https://www.propertyfinder.ae/en/buy/dubai/apartments-for-sale-dubai-marina-marina-shores.html
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Iterator

import urllib.request

SQFT_TO_M2 = 0.09290304

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)

# Признаки вида на воду в заголовке объявления
WATER_VIEW = re.compile(
    r"\b(sea|marina|water|ocean|palm|creek|canal|lagoon|full marina|waterfront)\s*view"
    r"|\bsea\b|\bmarina view\b|\bwater view\b",
    re.I,
)

# Славянские и среднеазиатские фамильные окончания — вторичный признак
SLAVIC_SUFFIX = re.compile(
    r"(ov|ova|ev|eva|in|ina|sky|skiy|skaya|enko|chuk|uk|yan|dze|shvili|bek|beva|baev|baeva)$",
    re.I,
)

SLAVIC_NAMES = {
    "aleksei", "alexey", "alexei", "andrei", "andrey", "anna", "anastasia", "anastasiia",
    "artem", "artyom", "daria", "darya", "dmitri", "dmitry", "dmitrii", "ekaterina",
    "elena", "evgenia", "evgeny", "galina", "igor", "ilya", "irina", "ivan", "julia",
    "kirill", "ksenia", "leonid", "liliya", "lyudmila", "maria", "mariia", "marina",
    "maxim", "mikhail", "nadezhda", "natalia", "nataliya", "nikita", "nikolai", "oksana",
    "olga", "oleg", "pavel", "polina", "roman", "ruslan", "sergei", "sergey", "svetlana",
    "tatiana", "tatyana", "vadim", "valeria", "vera", "victoria", "viktoria", "vladimir",
    "yana", "yulia", "zilola", "aigul", "azamat", "timur", "damir", "rustam", "farrukh",
    "madina", "dilnoza", "nodira", "gulnara", "askar", "bakhtiyor",
}


def fetch(url: str, timeout: int = 40) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_page(html: str) -> tuple[list[dict], int]:
    """Возвращает (объявления, всего найдено)."""
    match = NEXT_DATA.search(html)
    if not match:
        return [], 0
    data = json.loads(match.group(1))
    result = data.get("props", {}).get("pageProps", {}).get("searchResult", {})
    total = result.get("meta", {}).get("total_count", 0)
    return result.get("listings", []), total


def ru_score(agent: dict, title: str = "") -> tuple[int, str]:
    """Балл русскоязычности 0-3 и основание.

    3 — в профиле агента прямо указан русский язык (самый надёжный признак)
    2 — славянское имя или фамилия
    1 — русский текст в объявлении
    """
    languages = [lang.lower() for lang in (agent.get("languages") or [])]
    if "russian" in languages:
        return 3, "в профиле указан Russian"

    name = (agent.get("name") or "").strip()
    parts = [p for p in re.split(r"[\s\-]+", name) if p]
    first = parts[0].lower() if parts else ""
    last = parts[-1].lower() if len(parts) > 1 else ""

    if first in SLAVIC_NAMES:
        return 2, f"славянское имя ({parts[0]})"
    if last and SLAVIC_SUFFIX.search(last) and len(last) > 4:
        return 2, f"славянская фамилия ({parts[-1]})"
    if re.search(r"[а-яА-Я]", title):
        return 1, "русский текст в объявлении"

    return 0, ""


def normalize(listing: dict) -> dict | None:
    """Превратить сырое объявление в плоскую строку для таблицы."""
    prop = listing.get("property")
    if not prop:
        return None

    agent = prop.get("agent") or {}
    broker = prop.get("broker") or {}
    price = (prop.get("price") or {}).get("value")
    size_sqft = (prop.get("size") or {}).get("value")
    title = prop.get("title") or ""

    size_m2 = round(size_sqft * SQFT_TO_M2, 1) if size_sqft else None
    score, reason = ru_score(agent, title)

    return {
        "id": f"PF-{prop.get('id')}",
        "source": "Property Finder",
        "url": prop.get("share_url"),
        "title": title,
        "building": (prop.get("location") or {}).get("name"),
        "location": (prop.get("location") or {}).get("full_name"),
        "type": prop.get("property_type"),
        "bedrooms": prop.get("bedrooms"),
        "bathrooms": prop.get("bathrooms"),
        "size_sqft": size_sqft,
        "size_m2": size_m2,
        "price": price,
        "price_per_sqft": round(price / size_sqft) if price and size_sqft else None,
        "price_per_m2": round(price / size_m2) if price and size_m2 else None,
        "completion": prop.get("completion_status"),
        "furnished": prop.get("furnished"),
        "water_view": bool(WATER_VIEW.search(title)),
        "agent_name": agent.get("name"),
        "agent_languages": ", ".join(agent.get("languages") or []),
        "agent_url": (
            f"https://www.propertyfinder.ae/en/broker/{agent.get('slug')}-{agent.get('id')}"
            if agent.get("slug") else ""
        ),
        "ru_score": score,
        "ru_reason": reason,
        "agency": broker.get("name"),
        "agency_phone": broker.get("phone"),
        "agency_email": broker.get("email"),
        "verified": prop.get("is_verified"),
        "images_count": prop.get("images_count"),
        "listed_date": (prop.get("listed_date") or "")[:10],
        "reference": prop.get("reference"),
    }


def collect(base_url: str, max_pages: int = 10, pause: float = 1.2) -> Iterator[dict]:
    """Пройти по страницам выдачи и отдать нормализованные объявления."""
    seen: set[str] = set()

    for page in range(1, max_pages + 1):
        url = base_url if page == 1 else f"{base_url}?page={page}"
        listings, total = parse_page(fetch(url))
        if not listings:
            break

        for raw in listings:
            row = normalize(raw)
            if row and row["id"] not in seen:
                seen.add(row["id"])
                yield row

        if page * 24 >= total:
            break
        time.sleep(pause)
