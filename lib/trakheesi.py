"""DLD Trakheesi: карта разрешения на объявление и что из неё следует.

У каждого объявления на Property Finder есть ссылка на проверку разрешения
(`rera.permit_validation_url`) — та самая страница за QR-кодом. Сам номер
разрешения PF с 2025 года не показывает, но по ссылке DLD отдаёт
«Real Estate Permit Card»:

    Transaction Number, End Date              — номер и срок разрешения
    Authority Name, License #                 — агентство
    Broker: Name, Email, Mobile               — ЛИЧНЫЙ мобильный агента (не всегда)
    Building Name, Property Size (Sqm) 83.49  — точная площадь до сотых
    Property Value, Rooms, Zone

Точная площадь + здание — отпечаток конкретной квартиры. По нему:
  * находятся дубли: три брокера, одна квартира → одинаковые 83.49 м²
  * находится номер юнита в реестре DLD (data/dld_units/<здание>.csv)

Как читается карта. Приложение DLD ходит в свой API с reCAPTCHA v3 —
из скрипта запрос не сделать, и подделывать капчу мы не будем. Карта
читается в настоящем браузере, где капча отрабатывает сама, как у человека
со сканером QR. Текст страницы → parse_card().
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "dld_units"

FIELDS = {
    "Transaction Number": "permit_number",
    "End Date": "permit_until",
    "License #": "license",
    "Authority Name": "authority",
    "Name": "broker_name",
    "Email": "broker_email",
    "Mobile": "broker_mobile",
    "Property Name": "property_name",
    "Building Name": "building",
    "Property Type": "property_type",
    "Property Size(Sqm)": "size_sqm",
    "Zone Name": "zone",
    "Property Value(AED)": "value_aed",
    "Permit Type": "permit_type",
    "Rooms Count": "rooms",
    "Room Type": "room_type",
}


def parse_card(text: str) -> dict:
    """Разобрать текст страницы Trakheesi в поля. Пусто — если карты нет."""
    if "Real Estate Permit Card" not in text:
        return {}
    if "Listing not exist" in text or "has not been verified" in text:
        return {"verified": False}

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    card: dict = {"verified": True}
    for index, line in enumerate(lines[:-1]):
        key = FIELDS.get(line)
        if not key or key in card:
            continue
        value = lines[index + 1]
        if value in FIELDS:            # пустое поле: следующая строка — уже метка
            continue
        card[key] = value

    if card.get("size_sqm"):
        try:
            card["size_sqm"] = float(card["size_sqm"].replace(",", ""))
        except ValueError:
            pass
    if card.get("value_aed"):
        digits = re.sub(r"[^\d]", "", card["value_aed"])
        card["value_aed"] = int(digits) if digits else None
    if card.get("broker_mobile"):
        card["broker_mobile"] = normalize_phone(card["broker_mobile"])
    return card


def normalize_phone(raw: str) -> str:
    """0585759120 / 971543349698 / +971 54 … → +971543349698."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "971" + digits[1:]
    if len(digits) == 9 and digits.startswith("5"):
        digits = "971" + digits
    return f"+{digits}" if digits else ""


# ------------------------------------------------------------------ отпечаток

def fingerprint(building: str, size_sqm: float | None) -> str:
    """Ключ квартиры: здание + площадь до сотых."""
    if not building or not size_sqm:
        return ""
    return f"{building.strip().upper()}|{size_sqm:.2f}"


def find_duplicates(cards: dict[str, dict]) -> dict[str, list[str]]:
    """{listing_id: [другие listing_id той же квартиры]} по отпечатку."""
    groups: dict[str, list[str]] = defaultdict(list)
    for listing_id, card in cards.items():
        key = fingerprint(card.get("building", ""), card.get("size_sqm"))
        if key:
            groups[key].append(listing_id)
    result: dict[str, list[str]] = {}
    for members in groups.values():
        if len(members) > 1:
            for listing_id in members:
                result[listing_id] = [m for m in members if m != listing_id]
    return result


# ----------------------------------------------------------- реестр юнитов DLD

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def load_units(building: str) -> list[dict]:
    """Реестр юнитов здания из data/dld_units/<здание>.csv.

    Файл — выгрузка «Units → Download as CSV» с dubailand.gov.ae/en/open-data
    (за reCAPTCHA-галочкой, выгружает Алексей раз на здание) либо срез
    полного реестра с Dubai Pulse (dld_units-open, из ОАЭ без капчи).
    Колонки распознаются по названию: unit number / area / floor / rooms.
    """
    path = DATA_DIR / f"{_slug(building)}.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = []
        for raw in reader:
            row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
            unit = next((row[k] for k in row if "unit" in k and "number" in k), "")
            area = next((row[k] for k in row if "area" in k or "size" in k), "")
            floor = next((row[k] for k in row if "floor" in k), "")
            rooms = next((row[k] for k in row if "room" in k), "")
            try:
                area_f = float(area.replace(",", ""))
            except ValueError:
                area_f = None
            rows.append({"unit": unit, "area": area_f, "floor": floor, "rooms": rooms})
        return rows


def match_unit(building: str, size_sqm: float | None, tolerance: float = 0.02) -> list[dict]:
    """Юниты здания с такой же площадью. Несколько — стопка одинаковых на разных этажах."""
    if not size_sqm:
        return []
    return [u for u in load_units(building)
            if u["area"] is not None and abs(u["area"] - size_sqm) <= tolerance]


def describe_units(candidates: list[dict]) -> str:
    if not candidates:
        return ""
    if len(candidates) == 1:
        u = candidates[0]
        return f"юнит {u['unit']}" + (f", этаж {u['floor']}" if u["floor"] else "")
    units = ", ".join(u["unit"] for u in candidates[:6])
    more = f" +{len(candidates) - 6}" if len(candidates) > 6 else ""
    return f"{len(candidates)} кандидата одной площади: {units}{more}"
