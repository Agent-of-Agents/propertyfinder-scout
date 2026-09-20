"""Лист «Off-plan» в книге клиента: создание и сверка.

Те же правила, что у листа «Объекты» (lib/sync.py):
  - новая строка      → вставляется сверху, голубая заливка, 🆕 в комментарии
  - изменилась цена   → обновить, оранжевая заливка, 💰 было → стало
  - пропал из подбора → серая заливка, зачёркивание, ⚰️ (не удаляем)
  - колонки Алексея (✅ Одобрено, Статус, Мой комментарий) — не трогаем никогда

Якорь строки — listing_id = «project_id:тип» в скрытой колонке A.
"""

from __future__ import annotations

import datetime as dt
import re

from . import sheets
from .offplan import HEADERS, OWNER_COLUMNS
from .sync import _insert_top, _letter

SHEET_TITLE = "Off-plan"

# Колонки, изменение которых подсвечиваем и отмечаем в комментарии
WATCHED = ("Цена от AED", "Фаза продаж", "Сдача", "План оплаты")
# Системные колонки, которые при сверке не перезаписываем
KEEP_COLUMNS = {"listing_id", "Комментарий системы", "Презентация"}

# ------------------------------------------------------------------ оформление

HEADER_BG = {"red": 0.16, "green": 0.20, "blue": 0.26}     # тёмный графит
INK = {"red": 0.1, "green": 0.1, "blue": 0.1}
GREY = {"red": 0.6, "green": 0.6, "blue": 0.6}
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}


def _mark(view, row_number: int, removed: bool) -> dict:
    """Строгий документ (правило Алексея 20.09.2026): без заливок и зачёркиваний; выбывшие — ⚰️ в комментарии."""
    return {"repeatCell": {
        "range": {"sheetId": view.sheet_id, "startRowIndex": row_number - 1, "endRowIndex": row_number,
                  "startColumnIndex": 0, "endColumnIndex": len(view.header)},
        "cell": {"userEnteredFormat": {"backgroundColor": WHITE,
                                       "textFormat": {"strikethrough": False, "foregroundColor": INK}}},
        "fields": "userEnteredFormat(backgroundColor,textFormat.strikethrough,textFormat.foregroundColor)"}}
HEADER_FG = {"red": 1.0, "green": 1.0, "blue": 1.0}
GRID = {"red": 0.87, "green": 0.87, "blue": 0.87}

# Ширина колонок в пикселях — по названию, чтобы не зависеть от порядка
WIDTHS = {
    "listing_id": 40, "✅ Одобрено": 80, "Балл": 52, "Проект": 230, "Застройщик": 165, "Район": 150,
    "Локация": 210, "Фаза продаж": 135, "Старт продаж": 96, "Сдача": 78, "Стройка": 110, "Тип": 62,
    "Цена от AED": 112, "Площадь м²": 82, "AED/м²": 82, "К району": 78, "DLD AED/м²": 92, "К DLD": 70,
    "План оплаты": 270, "Взнос %": 66, "До ключей %": 78, "До ключей AED": 116, "На ключах %": 80,
    "После ключей %": 84, "Планировок": 78, "Ссылка": 96, "Брошюра": 96, "Презентация": 110,
    "Комментарий системы": 420, "Статус": 120, "Мой комментарий": 280,
    # лист «Объекты»
    "Приоритет": 70, "🇷🇺": 40, "Брокер": 150, "Агентство": 170, "Телефон": 120, "Языки": 130,
    "Цена AED": 110, "К рынку": 78, "Вид на воду": 90, "Цена/качество": 150, "Фото": 50,
    "📩 Запросить": 70, "Запрошено": 90, "Моя цена": 110,
    # каталог
    "Студия от": 100, "1BR от": 100, "2BR от": 100, "3BR от": 100, "4BR от": 100,
    "Сделок DLD": 80, "Интерес PF": 80, "Обновлено": 90,
}
CENTERED = {"✅ Одобрено", "Балл", "Тип", "Сдача", "Взнос %", "До ключей %", "На ключах %", "После ключей %",
            "К району", "К DLD", "Планировок", "Старт продаж", "Приоритет", "🇷🇺", "Фото", "📩 Запросить",
            "Запрошено", "К рынку", "Вид на воду"}
WRAPPED = {"План оплаты", "Комментарий системы", "Мой комментарий", "Локация", "Цена/качество", "Статус"}
MONEY = {"Цена от AED", "До ключей AED", "AED/м²", "DLD AED/м²", "Цена AED", "Моя цена",
         "Студия от", "1BR от", "2BR от", "3BR от", "4BR от"}
LINKS = {"Ссылка", "Брошюра", "Презентация"}
PERCENT_DELTA = {"К району", "К DLD", "К рынку"}   # пишем числом (0.07), показываем «+7%»
FREEZE_THROUGH = "Проект"      # закрепить колонки слева по эту включительно
FREEZE_THROUGH_ALT = "Брокер"  # для листа «Объекты»


def _col_range(sheet_id: int, index: int, first_row: int = 1) -> dict:
    return {"sheetId": sheet_id, "startRowIndex": first_row, "startColumnIndex": index, "endColumnIndex": index + 1}


def style_sheet(spreadsheet_id: str, sheet_id: int, header: list[str]) -> None:
    """Шапка, закрепление, ширины, форматы чисел, выравнивание, шкала балла.

    Только форматирование — значения не трогает, можно применять к живому листу.
    """
    requests: list[dict] = []
    n = len(header)

    # шапка: тёмный фон, белый жирный текст, перенос, по центру, высота 46
    requests.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": n},
        "cell": {"userEnteredFormat": {
            "backgroundColor": HEADER_BG,
            "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": HEADER_FG},
            "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE", "horizontalAlignment": "CENTER"}},
        "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment,horizontalAlignment)"}})
    requests.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 46}, "fields": "pixelSize"}})

    # тело: вертикально по центру, обрезка длинного текста, шрифт 10
    requests.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": n},
        "cell": {"userEnteredFormat": {"verticalAlignment": "MIDDLE", "wrapStrategy": "CLIP",
                                       "textFormat": {"fontSize": 10}}},
        "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat.fontSize)"}})

    for index, name in enumerate(header):
        width = WIDTHS.get(name)
        if width:
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": index, "endIndex": index + 1},
                "properties": {"pixelSize": width}, "fields": "pixelSize"}})
        fmt: dict = {}
        if name in CENTERED:
            fmt["horizontalAlignment"] = "CENTER"
        if name in WRAPPED:
            fmt["wrapStrategy"] = "WRAP"
        if name in MONEY:
            fmt["numberFormat"] = {"type": "NUMBER", "pattern": "#,##0"}
            fmt["horizontalAlignment"] = "RIGHT"
        if name == "Площадь м²":
            fmt["numberFormat"] = {"type": "NUMBER", "pattern": "0.0"}
        if name in PERCENT_DELTA:
            fmt["numberFormat"] = {"type": "NUMBER", "pattern": "+0%;-0%;0%"}
        if name in LINKS:
            fmt["textFormat"] = {"fontSize": 9, "foregroundColor": {"red": 0.1, "green": 0.35, "blue": 0.7}}
        if fmt:
            requests.append({"repeatCell": {"range": _col_range(sheet_id, index), "cell": {"userEnteredFormat": fmt},
                                            "fields": "userEnteredFormat(" + ",".join(fmt) + ")"}})

    # закрепить шапку и колонки слева до «Проект» (или «Брокер»)
    anchor = next((header.index(c) for c in (FREEZE_THROUGH, FREEZE_THROUGH_ALT) if c in header), 1)
    requests.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": anchor + 1}},
        "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}})

    # тонкая сетка по всему листу
    requests.append({"updateBorders": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": n},
        "innerHorizontal": {"style": "SOLID", "width": 1, "color": GRID},
        "innerVertical": {"style": "SOLID", "width": 1, "color": GRID}}})

    # старые правила условного форматирования убираем
    meta = sheets.get_spreadsheet(spreadsheet_id)
    existing_rules = next((len(sh.get("conditionalFormats") or []) for sh in meta.get("sheets", [])
                           if sh["properties"]["sheetId"] == sheet_id), 0)
    for _ in range(existing_rules):
        requests.append({"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": 0}})
    # шкалы и заливки не используем — Алексей просил строгий вид документа (11.09.2026)

    sheets.batch_update(spreadsheet_id, requests)


def migrate_header(spreadsheet_id: str, title: str) -> bool:
    """Добавить в шапку колонки из HEADERS, которых ещё нет — на их законное место.

    Вставка колонки сдвигает соседей, но Google переносит данные и формулы сам.
    Возвращает True, если что-то добавили.
    """
    view = View(spreadsheet_id, title)
    added = False
    for position, name in enumerate(HEADERS):
        if name in view.col:
            continue
        # вставляем сразу после предыдущей известной колонки
        prev = next((view.col[h] for h in reversed(HEADERS[:position]) if h in view.col), -1)
        at = prev + 1
        sheets.insert_columns(spreadsheet_id, view.sheet_id, at, 1)
        sheets.update_range(spreadsheet_id, f"{title}!{_letter(at)}1", [[name]])
        view.reload()
        added = True
    return added


class View:
    """Снимок листа: шапка, строки, индексы колонок по названию."""

    def __init__(self, spreadsheet_id: str, title: str = SHEET_TITLE):
        self.spreadsheet_id, self.title = spreadsheet_id, title
        self.reload()

    def reload(self) -> None:
        rows = sheets.read_range(self.spreadsheet_id, f"{self.title}!A1:AZ2000")
        self.header = [h.strip() for h in rows[0]] if rows else []
        self.rows = rows[1:] if rows else []
        self.col = {name: i for i, name in enumerate(self.header)}
        self.sheet_id = {s["title"]: s["sheet_id"] for s in sheets.list_sheets(self.spreadsheet_id)}[self.title]

    def cell(self, row: list, name: str) -> str:
        i = self.col.get(name)
        if i is None or i >= len(row):
            return ""
        return row[i].strip() if isinstance(row[i], str) else str(row[i])

    def a1(self, row_number: int, name: str) -> str:
        return f"{self.title}!{_letter(self.col[name])}{row_number}"

    def guard(self, name: str) -> None:
        if name in OWNER_COLUMNS:
            raise RuntimeError(f"Отказ записи: «{name}» — колонка Алексея")
        if name not in self.col:
            raise RuntimeError(f"В шапке листа «{self.title}» нет колонки «{name}»")


def ensure_sheet(spreadsheet_id: str, title: str = SHEET_TITLE) -> int:
    """Создать лист с шапкой, чекбоксами и скрытой колонкой A, если его ещё нет."""
    existing = {s["title"]: s["sheet_id"] for s in sheets.list_sheets(spreadsheet_id)}
    if title in existing:
        return existing[title]
    sheet_id = sheets.create_sheet(spreadsheet_id, title, rows=2000, columns=len(HEADERS) + 5)
    sheets.update_range(spreadsheet_id, f"{title}!A1", [HEADERS])
    sheets.freeze_header(spreadsheet_id, sheet_id, rows=1)
    sheets.set_checkbox_column(spreadsheet_id, sheet_id, column_index=HEADERS.index("✅ Одобрено"),
                               first_row=1, last_row=2000)
    sheets.batch_update(spreadsheet_id, [
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
    ])
    style_sheet(spreadsheet_id, sheet_id, HEADERS)
    return sheet_id


HIGHLIGHT_DAYS = 3
_MARKER = re.compile(r"^((?:🆕|💰|🔁)[^;]*?\b(\d{2})\.(\d{2})\)?)(?:;|$)")


def _marker(comment: str) -> str:
    """«🆕 10.09» или «💰 цена: … → … (10.09)» из начала комментария, если есть."""
    m = _MARKER.match(comment or "")
    return m.group(1) if m else ""


def _marker_age(marker: str, today: dt.date) -> int:
    m = re.search(r"(\d{2})\.(\d{2})", marker)
    if not m:
        return HIGHLIGHT_DAYS
    day, month = int(m.group(1)), int(m.group(2))
    year = today.year if month <= today.month else today.year - 1
    try:
        return (today - dt.date(year, month, day)).days
    except ValueError:
        return HIGHLIGHT_DAYS


def _fmt(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def sync(spreadsheet_id: str, rows: list[dict], title: str = SHEET_TITLE,
         today: dt.date | None = None) -> dict:
    """Свести подбор с листом. Возвращает сводку {new, changed, removed, kept}."""
    today = today or dt.date.today()
    stamp = today.strftime("%d.%m")
    ensure_sheet(spreadsheet_id, title)
    if migrate_header(spreadsheet_id, title):
        pass
    view = View(spreadsheet_id, title)
    style_sheet(spreadsheet_id, view.sheet_id, view.header)

    missing = [h for h in HEADERS if h not in view.col]
    if missing:
        raise RuntimeError(f"В шапке «{title}» нет колонок {missing} — остановка до первой записи")

    fresh = {r["listing_id"]: r for r in rows}
    existing_ids = {view.cell(r, "listing_id") for r in view.rows}
    report = {"new": [], "changed": [], "removed": [], "kept": 0}

    # ---------------------------------------------------------- 1. новые — сверху
    new_rows = [r for r in rows if r["listing_id"] not in existing_ids]
    if new_rows:
        _insert_top(view, len(new_rows))
        view.reload()
        updates, formats = {}, []
        for offset, r in enumerate(new_rows):
            number = 2 + offset
            values = {k: v for k, v in r.items() if k in view.col and k not in OWNER_COLUMNS}
            values["Комментарий системы"] = f"🆕 {stamp}; {r['Комментарий системы']}"
            for name, value in values.items():
                view.guard(name)
                updates[view.a1(number, name)] = [[value]]
            formats.append(_mark(view, number, removed=False))
            report["new"].append({"id": r["listing_id"], "title": r["Проект"], "type": r["Тип"], "price": r["Цена от AED"]})
        sheets.update_ranges(spreadsheet_id, updates)
        formats.append({"setDataValidation": {
            "range": {"sheetId": view.sheet_id, "startRowIndex": 1, "endRowIndex": 1 + len(new_rows),
                      "startColumnIndex": view.col["✅ Одобрено"], "endColumnIndex": view.col["✅ Одобрено"] + 1},
            "rule": {"condition": {"type": "BOOLEAN"}, "strict": True}}})
        sheets.batch_update(spreadsheet_id, formats)
        view.reload()

    # ---------------------------------------------------------- 2. существующие
    updates, formats = {}, []
    new_ids = {r["listing_id"] for r in new_rows}
    for index, row in enumerate(view.rows):
        listing_id = view.cell(row, "listing_id")
        if not listing_id or listing_id in new_ids:
            continue
        number = index + 2
        comment = view.cell(row, "Комментарий системы")
        fresh_row = fresh.get(listing_id)

        if fresh_row is None:
            if "⚰️" not in comment:
                updates[view.a1(number, "Комментарий системы")] = [[f"⚰️ выбыл {stamp}; {comment}"]]
                formats.append(_mark(view, number, removed=True))
                report["removed"].append({"id": listing_id, "title": view.cell(row, "Проект")})
            continue

        notes = []
        if "⚰️" in comment:
            # Был выбывшим, снова прошёл фильтр — снимаем зачёркивание
            notes.append(f"↩️ вернулся")
            comment = comment.split(";", 1)[-1].strip()
        for name in WATCHED:
            old, new = view.cell(row, name), str(fresh_row.get(name, ""))
            old_n, new_n = old.replace(" ", "").replace(",", ""), new.replace(" ", "").replace(",", "")
            if old_n != new_n and (old_n or new_n):
                if name == "Цена от AED":
                    notes.append(f"💰 цена: {_fmt(old) if old else '—'} → {_fmt(new)}")
                else:
                    notes.append(f"🔁 {name.lower()}: {old or '—'} → {new or '—'}")
        for name in HEADERS:
            if name in OWNER_COLUMNS or name in KEEP_COLUMNS:
                continue
            view.guard(name)
            updates[view.a1(number, name)] = [[fresh_row.get(name, "")]]

        base = fresh_row["Комментарий системы"]
        if notes:
            updates[view.a1(number, "Комментарий системы")] = [[f"{' '.join(notes)} ({stamp}); {base}"]]
            formats.append(_mark(view, number, removed=False))
            report["changed"].append({"id": listing_id, "title": fresh_row["Проект"], "notes": notes})
        else:
            # Пометка 🆕/💰 живёт HIGHLIGHT_DAYS, потом заливка снимается, комментарий обновляется
            marker = _marker(comment)
            if marker and _marker_age(marker, today) < HIGHLIGHT_DAYS:
                updates[view.a1(number, "Комментарий системы")] = [[f"{marker}; {base}"]]
            else:
                pass
                updates[view.a1(number, "Комментарий системы")] = [[base]]
            report["kept"] += 1

    if updates:
        sheets.update_ranges(spreadsheet_id, updates)
    if formats:
        sheets.batch_update(spreadsheet_id, formats)
    return report
