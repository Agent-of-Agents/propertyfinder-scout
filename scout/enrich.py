"""Обогащение листа подбора после сверки: серия, дубли, колонки DLD.

Что Алексей ждёт в каждой книге (лист Собитовой, 12–14.09.2026):
    DLD м² · Тел. брокера DLD · Email DLD · Разрешение до · Юнит DLD · Дубли · Серия

Кто что заполняет:
  * Серия — из заголовка и описания объявления (lib.layout): «серия 07 · тип C · юнит 2304».
    Описание есть на странице списка PF, карточка объявления не нужна. Считается здесь, на сервере.
  * Дубли ≈ — одна квартира у нескольких брокеров: тот же дом, те же спальни, та же площадь
    до квадратного фута. Считается здесь по выгрузке; строки помечаются «≈ цена · брокер · ссылка».
  * DLD м², телефон, email, разрешение, юнит и «✔» в дублях — из карты DLD Trakheesi.
    Карта за reCAPTCHA v3, читается только настоящим браузером на ПК (scripts/dld_lookup.py);
    капчу не обходим. Колонки создаются здесь, чтобы ПК-шаг писал в них по названию.

Пишем только в свои колонки и только по названию (lib.sync.SheetView.guard).
Строки с «✔» из DLD не трогаем — они точнее наших «≈».
"""

from __future__ import annotations

import logging
from collections import defaultdict

from lib import layout, sheets, sync

from .models import Client, Search

log = logging.getLogger("scout.enrich")

DLD_COLUMNS = ["DLD м²", "Тел. брокера DLD", "Email DLD", "Разрешение до", "Юнит DLD"]
DUPES_COLUMN, SERIES_COLUMN = "Дубли", "Серия"
EXTRA_COLUMNS = DLD_COLUMNS + [DUPES_COLUMN, SERIES_COLUMN]


def money(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value or "")


def fingerprint(row: dict) -> str | None:
    """Отпечаток квартиры по данным объявления: дом · спальни · площадь в sqft."""
    if not row.get("building") or row.get("size_sqft") is None or row.get("bedrooms") is None:
        return None
    return f"{str(row['building']).strip().lower()}|{row['bedrooms']}|{int(round(float(row['size_sqft'])))}"


def find_dupes(rows: list[dict]) -> dict[str, list[dict]]:
    """listing_id → другие объявления той же квартиры (по отпечатку)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        fp = fingerprint(r)
        if fp:
            groups[fp].append(r)
    result: dict[str, list[dict]] = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        for r in group:
            others = [o for o in group if o["id"] != r["id"]]
            result[r["id"]] = sorted(others, key=lambda o: o.get("price") or 0)
    return result


def dupes_text(others: list[dict]) -> str:
    return "\n".join(f"≈ {money(o.get('price'))} AED · {o.get('agent_name') or ''} · {o.get('url') or ''}".strip(" ·")
                     for o in others)


def series_text(row: dict) -> str:
    info = layout.extract(row.get("title") or "", row.get("description") or "")
    return layout.label(info) or "—"


def ensure_columns(view: sync.SheetView) -> None:
    """Дописать недостающие колонки в конец шапки; лист при необходимости расширить."""
    missing = [c for c in EXTRA_COLUMNS if c not in view.col]
    if not missing:
        return
    start = len(view.header)
    meta = {s["title"]: s for s in sheets.list_sheets(view.spreadsheet_id)}.get(view.title, {})
    have = meta.get("columns") or 26
    if start + len(missing) > have:
        sheets.batch_update(view.spreadsheet_id, [{"appendDimension": {
            "sheetId": view.sheet_id, "dimension": "COLUMNS", "length": start + len(missing) - have}}])
    sheets.update_range(view.spreadsheet_id, f"'{view.title}'!{sync._letter(start)}1", [missing])
    view.reload()


def apply(client: Client, search: Search, rows: list[dict]) -> dict:
    """Серия и дубли ≈ по выгрузке — в лист подбора. Возвращает счётчики."""
    view = sync.SheetView(client.spreadsheet_id, search.title)
    ensure_columns(view)
    by_id = {r["id"]: r for r in rows if r.get("id")}
    dupes = find_dupes(rows)
    updates: dict[str, list] = {}
    stats = {"series": 0, "dupes": 0}

    for offset, sheet_row in enumerate(view.rows):
        lid = view.cell(sheet_row, "listing_id")
        row = by_id.get(lid)
        if not row:
            continue
        row_number = offset + 2

        current = view.cell(sheet_row, SERIES_COLUMN)
        text = series_text(row)
        if text != "—" and text != current:
            view.guard(SERIES_COLUMN)
            updates[view.a1(row_number, SERIES_COLUMN)] = [[text]]
            stats["series"] += 1
        elif not current:
            view.guard(SERIES_COLUMN)
            updates[view.a1(row_number, SERIES_COLUMN)] = [["—"]]

        current = view.cell(sheet_row, DUPES_COLUMN)
        if "✔" in current:            # подтверждено картой DLD — точнее нашего «≈»
            continue
        text = dupes_text(dupes.get(lid, []))
        if text != current:
            view.guard(DUPES_COLUMN)
            updates[view.a1(row_number, DUPES_COLUMN)] = [[text]]
            if text:
                stats["dupes"] += 1

    if updates:
        sheets.update_ranges(view.spreadsheet_id, updates)
    stats["dupe_groups"] = len({fingerprint(r) for r in rows if r["id"] in dupes})
    return stats


def card_extras(row: dict, rows: list[dict]) -> dict:
    """Серия и число дублей — для карточки объекта в Telegram."""
    extras = {}
    text = series_text(row)
    if text != "—":
        extras["series"] = text
    others = find_dupes(rows).get(row.get("id", ""), [])
    if others:
        extras["dupes"] = len(others)
    return extras
