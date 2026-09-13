"""Карточка объявления Property Finder: полные детали и фотографии.

Страница объявления отдаёт всё в __NEXT_DATA__, включая фото в 1312×894.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

from .propertyfinder import NEXT_DATA, SQFT_TO_M2, UA, fetch

FLOOR_WORDS = {
    "high floor": "высокий этаж",
    "mid floor": "средний этаж",
    "middle floor": "средний этаж",
    "low floor": "низкий этаж",
    "podium": "подиум",
}


def fetch_detail(url: str) -> dict:
    """Полная карточка объявления: характеристики, удобства, ссылки на фото."""
    data = json.loads(NEXT_DATA.search(fetch(url)).group(1))
    prop = data["props"]["pageProps"]["propertyResult"]["property"]

    raw_images = (prop.get("images") or {}).get("property", [])
    images = [i.get("full") or i.get("medium") for i in raw_images]
    images = [i for i in images if i]

    # Планировка. Три источника по надёжности:
    #  1. поле floor_plans — агент загрузил её отдельно
    #  2. фото с меткой classification_label про план
    #  3. визуальный отбор среди фото (см. looks_like_plan)
    plans: list[str] = []
    plan_units: list[str] = []
    for plan in prop.get("floor_plans") or []:
        if isinstance(plan, dict):
            # PF кладёт картинку в image_url — из-за этого планировки раньше терялись
            url = plan.get("image_url") or plan.get("full") or plan.get("url") or plan.get("image")
            if url:
                plans.append(url)
            if plan.get("unit_number"):
                plan_units.append(str(plan["unit_number"]))
        elif isinstance(plan, str):
            plans.append(plan)

    for image in raw_images:
        label = (image.get("classification_label") or "").lower()
        if "plan" in label or "layout" in label:
            url = image.get("full") or image.get("medium")
            if url and url not in plans:
                plans.append(url)

    amenities = []
    for group in data["props"]["pageProps"].get("amenitiesGrouped") or []:
        for item in group.get("amenities", []):
            name = item.get("name")
            if name:
                amenities.append(name)

    size_sqft = (prop.get("size") or {}).get("value")
    price = (prop.get("price") or {}).get("value")
    title = prop.get("title") or ""

    floor = ""
    for needle, label in FLOOR_WORDS.items():
        if needle in title.lower():
            floor = label
            break

    return {
        "id": f"PF-{prop.get('id')}",
        "url": url,
        "title": title,
        "description": prop.get("description") or "",
        "building": (prop.get("location") or {}).get("name"),
        "location": (prop.get("location") or {}).get("full_name"),
        "type": prop.get("property_type"),
        "bedrooms": prop.get("bedrooms"),
        "bathrooms": prop.get("bathrooms"),
        "size_sqft": size_sqft,
        "size_m2": round(size_sqft * SQFT_TO_M2, 1) if size_sqft else None,
        "price": price,
        "completion": prop.get("completion_status"),
        "furnished": prop.get("furnished"),
        "floor": floor,
        "amenities": amenities,
        "images": images,
        "floor_plans": plans,
        "plan_unit_numbers": plan_units,
        "permit_validation_url": (prop.get("rera") or {}).get("permit_validation_url") or "",
        "agent": (prop.get("agent") or {}).get("name"),
        "agency": (prop.get("broker") or {}).get("name"),
        "agency_phone": (prop.get("broker") or {}).get("phone"),
        "reference": prop.get("reference"),
    }


def looks_like_plan(path: Path) -> bool:
    """Похоже ли фото на планировку — только как подсказка, не как решение.

    Планировка — это чертёж: больше половины кадра почти белый фон,
    поверх тонкие линии. Светлая кухня или ванная тоже яркие и малонасыщенные,
    поэтому одной яркости мало — считаем именно долю почти белых пикселей.

    Ошибиться тут дорого: фото санузла под заголовком Floor Plan в презентации
    клиенту хуже, чем отсутствие слайда. Поэтому результат идёт Алексею
    на подтверждение, а не прямо в презентацию.
    """
    try:
        from PIL import Image
    except ImportError:
        return False

    try:
        image = Image.open(path).convert("RGB")
    except Exception:  # noqa: BLE001
        return False

    image.thumbnail((200, 200))
    pixels = list(image.getdata())
    if not pixels:
        return False

    near_white = sum(
        1 for r, g, b in pixels
        if r > 232 and g > 232 and b > 232 and max(r, g, b) - min(r, g, b) < 14
    )
    return near_white / len(pixels) > 0.45


def download_images(urls: list[str], target: Path, limit: int = 12,
                    pause: float = 0.4) -> list[Path]:
    """Скачать фото в папку. Возвращает пути к скачанным файлам."""
    target.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    for index, url in enumerate(urls[:limit], start=1):
        path = target / f"{index:02d}.jpg"
        if path.exists() and path.stat().st_size > 0:
            saved.append(path)
            continue

        request = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                path.write_bytes(response.read())
            saved.append(path)
        except Exception as error:  # noqa: BLE001
            print(f"  не скачалось фото {index}: {error}")
        time.sleep(pause)

    return saved
