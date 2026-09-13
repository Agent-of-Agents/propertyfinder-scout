"""Серия / тип планировки из текста объявления.

Брокеры вписывают серию сами, руками, в заголовок или в список характеристик:
    BEST LAYOUT 07 - MID FLOOR         → серия 07
    * 01 Series                        → серия 01
    The 10th series layout             → серия 10
    * Type C   /   Type E 2 Bedroom    → тип C / E
    Unit 2304                          → номер юнита прямо в тексте (редко, но бывает)

Слова «spacious layout», «open-plan layout» — шум, серии в них нет.
Проверено на 89 объявлениях Marina Shores: серия названа у ~10%.
"""

from __future__ import annotations

import re

SERIES = [
    re.compile(r"\b(\d{1,2})\s*(?:th|st|nd|rd)?[\s-]*series\b", re.I),   # 01 Series, 10th series
    re.compile(r"\bseries\s*[:#-]?\s*(\d{1,2})\b", re.I),                  # Series 07
    re.compile(r"\blayout\s*[:#-]?\s*(\d{2})\b", re.I),                     # LAYOUT 07
    re.compile(r"\b(\d{2})\s*layout\b", re.I),                              # 07 LAYOUT
    re.compile(r"\bstack\s*[:#-]?\s*(\d{1,2})\b", re.I),                    # Stack 07
]

# «Type C», «Type E 2 Bedroom» — буква, но не «Type: Apartment» и не «Type of»
UNIT_TYPE = re.compile(r"\btype\s*[:#-]?\s*([A-H])(?![A-Za-z])", re.I)

# Номер юнита, если брокер его написал: Unit 2304, Unit No. 1507, Apt 905
UNIT_NUMBER = re.compile(
    r"\b(?:unit|apt|apartment|flat)\s*(?:no\.?|number|#)?\s*[:\-]?\s*(\d{3,4})\b(?!\s*(?:sq|sqft|sq\.))",
    re.I,
)


def extract(title: str, description: str = "") -> dict:
    """{'series': '07', 'type': 'C', 'unit': '2304', 'snippet': '…'} — что нашлось."""
    text = f"{title or ''}\n{description or ''}"
    text = re.sub(r"<[^>]+>", " ", text)
    found: dict = {}

    for pattern in SERIES:
        m = pattern.search(text)
        if m:
            found["series"] = m.group(1).zfill(2)
            found["snippet"] = _around(text, m)
            break

    m = UNIT_TYPE.search(text)
    if m:
        found["type"] = m.group(1).upper()
        found.setdefault("snippet", _around(text, m))

    m = UNIT_NUMBER.search(text)
    if m:
        found["unit"] = m.group(1)
        found.setdefault("snippet", _around(text, m))

    return found


def _around(text: str, m: re.Match, span: int = 28) -> str:
    start, end = max(0, m.start() - span), min(len(text), m.end() + span)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def label(found: dict) -> str:
    """Короткая подпись для колонки: «серия 07 · тип C · юнит 2304»."""
    parts = []
    if found.get("series"):
        parts.append(f"серия {found['series']}")
    if found.get("type"):
        parts.append(f"тип {found['type']}")
    if found.get("unit"):
        parts.append(f"юнит {found['unit']}")
    return " · ".join(parts)
