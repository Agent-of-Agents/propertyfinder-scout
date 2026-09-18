"""Сбор объявлений с Property Finder (Дубай).

Данные лежат прямо в странице, в блоке __NEXT_DATA__ — браузер не нужен.
Работает по страницам башни или района, например:
  https://www.propertyfinder.ae/en/buy/dubai/apartments-for-sale-dubai-marina-marina-shores.html
"""

from __future__ import annotations

import json
import os
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


# Что PF за CloudFront закрывает с IP серверов (проверено 18.09.2026 с DigitalOcean):
# карточки объявлений /en/plp/… и шлюз /leads/… — 403; страницы списка и фото — 200.
# Для закрытых путей запрос идёт через PF_PROXY_URL, если он задан (http://user:pass@host:port).
PROXIED_PREFIXES = ("/en/plp/", "/ar/plp/", "/leads/")
FULL_IMAGE_SIZE = "1312x894"


def needs_proxy(url: str) -> bool:
    return any(p in url for p in PROXIED_PREFIXES)


def opener_for(url: str) -> urllib.request.OpenerDirector:
    proxy = os.environ.get("PF_PROXY_URL", "").strip()
    if proxy and needs_proxy(url):
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


def urlopen(request: urllib.request.Request, timeout: int = 40):
    """urlopen с учётом прокси для закрытых путей PF."""
    return opener_for(request.full_url).open(request, timeout=timeout)


def fetch(url: str, timeout: int = 40) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def full_image(url: str) -> str:
    """Со страницы списка PF отдаёт 668x452; полный размер лежит по тому же пути, хеш v= не проверяется."""
    return re.sub(r"/\d+x\d+\.jpg", f"/{FULL_IMAGE_SIZE}.jpg", url or "")


def list_images(prop: dict) -> tuple[list[str], list[str]]:
    """Фото и планировки объявления из данных страницы списка — карточка объявления не нужна."""
    images, plans = [], []
    for image in prop.get("images") or []:
        if not isinstance(image, dict):
            continue
        url = image.get("medium") or image.get("small") or image.get("full")
        if not url:
            continue
        label = (image.get("classification_label") or "").lower()
        (plans if ("plan" in label or "layout" in label) else images).append(full_image(url))
    for plan in prop.get("floor_plans") or []:
        if isinstance(plan, dict):
            url = plan.get("image_url") or plan.get("full") or plan.get("url") or plan.get("image")
            if url and url not in plans:
                plans.insert(0, url)
        elif isinstance(plan, str) and plan not in plans:
            plans.insert(0, plan)
    return images, plans


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
    images, plans = list_images(prop)

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
        # Всё, за чем раньше ходили на карточку объявления — она с сервера закрыта (403)
        "pf_listing_id": prop.get("listing_id") or "",
        "agent_id": str(agent.get("id") or ""),
        "broker_id": str(broker.get("id") or ""),
        "images": images,
        "floor_plans": plans,
        "description": prop.get("description") or "",
        "amenities": list(prop.get("amenity_names") or []),
        "rera_permit": ((prop.get("rera") or {}).get("permit_validation_url") or "") if isinstance(prop.get("rera"), dict) else "",
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
