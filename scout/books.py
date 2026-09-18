"""Книги Google клиента и общая «Панель» (docs/HANDOFF.md §6).

Книга клиента: «Сводка» первым листом, лист на каждый подбор, «Дистресс», «Лог».
Закрытый подбор не удаляется — лист переименовывается в «✕ …» или «✓ …»,
ярлык серый, лист уезжает в конец книги.

Всё создаётся на Общем диске от имени сервисного аккаунта: в «Мой диск» он
писать не может (нулевая квота), см. lib/drive.create_spreadsheet_in_folder.
"""

from __future__ import annotations

import datetime as dt

from lib import distress, drive, offplan_sheet, sheets
from lib.google_auth import working_folder_id

from .models import (
    CLOSE_BOUGHT,
    MARKET_OFFPLAN,
    MARKET_SECONDARY,
    Client,
    Search,
)

# Шапка листа подбора secondary — порядок из HANDOFF §6.2. Колонки ищутся по
# названию, так что порядок важен только при создании.
SECONDARY_HEADERS = [
    "listing_id", "✅ Одобрено", "📩 Запросить", "Запрошено", "Приоритет", "🇷🇺",
    "Брокер", "Агентство", "Телефон", "Языки", "Тип", "Площадь м²", "Цена AED", "AED/м²",
    "К рынку", "Вид на воду", "Цена/качество", "Фото", "Ссылка", "Комментарий системы",
    "Статус", "Моя цена", "Мой комментарий", "Презентация",
    # Обогащение (scout/enrich.py): DLD — с ПК по карте Trakheesi, дубли и серия — сервером
    "DLD м²", "Тел. брокера DLD", "Email DLD", "Разрешение до", "Юнит DLD", "Дубли", "Серия",
]
SECONDARY_CHECKBOXES = ("✅ Одобрено", "📩 Запросить")
# Колонки Алексея — система не пишет в них по расписанию никогда.
OWNER_COLUMNS = {"✅ Одобрено", "📩 Запросить", "Статус", "Моя цена", "Мой комментарий"}

SUMMARY_SHEET, DISTRESS_SHEET, LOG_SHEET = "Сводка", "Дистресс", "Лог"
SUMMARY_HEADERS = ["Подбор", "Рынок", "Статус", "Объектов", "🆕 сегодня", "Одобрено",
                   "Запрошено", "Презентаций", "Последний прогон", "Лист"]
LOG_HEADERS = ["Дата", "Подбор", "Событие"]

PANEL_TITLE = "Панель — Scout Dubai"
PANEL_SHEET = "Подборы"
PANEL_HEADERS = ["Клиент", "Подбор", "Рынок", "Статус", "Объектов", "Одобрено", "Запрошено",
                 "Презентаций", "Последний прогон", "Сделка/приезд", "Книга", "Тема"]

GREY = {"red": 0.62, "green": 0.62, "blue": 0.62}


def sheet_url(spreadsheet_id: str, sheet_id: int | None = None) -> str:
    url = sheets.spreadsheet_url(spreadsheet_id)
    return f"{url}#gid={sheet_id}" if sheet_id is not None else url


# ------------------------------------------------------------------ книга клиента

def create_client_book(client: Client) -> str:
    """Книга клиента с постоянными листами. Возвращает spreadsheet_id."""
    created = drive.create_spreadsheet_in_folder(f"{client.name} — подбор", working_folder_id())
    sid = created["id"]
    sheets.create_sheet(sid, SUMMARY_SHEET, rows=200, columns=len(SUMMARY_HEADERS) + 2)
    sheets.update_range(sid, f"{SUMMARY_SHEET}!A1", [SUMMARY_HEADERS])
    sheets.create_sheet(sid, DISTRESS_SHEET, rows=500, columns=len(distress.HEADERS) + 2)
    sheets.update_range(sid, f"{DISTRESS_SHEET}!A1", [distress.HEADERS])
    sheets.create_sheet(sid, LOG_SHEET, rows=2000, columns=3)
    sheets.update_range(sid, f"{LOG_SHEET}!A1", [LOG_HEADERS])
    # Лист, который Google создаёт по умолчанию, — лишний
    for sheet in sheets.list_sheets(sid):
        if sheet["title"] in ("Sheet1", "Лист1"):
            sheets.delete_sheet(sid, sheet["sheet_id"])
    return sid


def create_search_sheet(client: Client, search: Search) -> int:
    """Лист подбора с шапкой под рынок. Возвращает sheetId."""
    sid = client.spreadsheet_id
    if search.market == MARKET_OFFPLAN:
        return offplan_sheet.ensure_sheet(sid, search.title)

    existing = {s["title"]: s["sheet_id"] for s in sheets.list_sheets(sid)}
    if search.title in existing:
        return existing[search.title]
    sheet_id = sheets.create_sheet(sid, search.title, rows=2000, columns=len(SECONDARY_HEADERS) + 4)
    sheets.update_range(sid, f"{search.title}!A1", [SECONDARY_HEADERS])
    sheets.freeze_header(sid, sheet_id, rows=1)
    for name in SECONDARY_CHECKBOXES:
        sheets.set_checkbox_column(sid, sheet_id, column_index=SECONDARY_HEADERS.index(name),
                                   first_row=1, last_row=2000)
    sheets.batch_update(sid, [{
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}}])
    offplan_sheet.style_sheet(sid, sheet_id, SECONDARY_HEADERS)
    _place_after_summary(sid, sheet_id)
    return sheet_id


def close_search_sheet(client: Client, search: Search, reason: str) -> None:
    """«✕ Название» или «✓ Название» (куплено), серый ярлык, в конец книги."""
    sid = client.spreadsheet_id
    sheet = sheets.find_sheet(sid, search.title)
    if not sheet:
        return
    mark = "✓" if reason == CLOSE_BOUGHT else "✕"
    total = len(sheets.list_sheets(sid))
    sheets.batch_update(sid, [{
        "updateSheetProperties": {
            "properties": {"sheetId": sheet["sheet_id"], "title": f"{mark} {search.title}",
                           "tabColor": GREY, "index": total},
            "fields": "title,tabColor,index"}}])


def _place_after_summary(sid: str, sheet_id: int) -> None:
    """Листы подборов идут сразу за «Сводкой», перед «Дистресс» и «Лог»."""
    titles = [s["title"] for s in sheets.list_sheets(sid)]
    fixed_tail = [t for t in titles if t in (DISTRESS_SHEET, LOG_SHEET) or t[:1] in "✕✓"]
    index = len(titles) - len(fixed_tail) - 1
    if index < 1:
        return
    sheets.batch_update(sid, [{
        "updateSheetProperties": {"properties": {"sheetId": sheet_id, "index": index},
                                  "fields": "index"}}])


# ------------------------------------------------------------------ сводка, лог

def search_stats(client: Client, search: Search) -> dict:
    """Счётчики по листу подбора: объектов, одобрено, запрошено, презентаций."""
    rows = sheets.read_range(client.spreadsheet_id, f"'{search.title}'!A1:AZ2000")
    if not rows:
        return {"objects": 0, "approved": 0, "requested": 0, "pdf": 0, "new_today": 0}
    header = rows[0]
    body = [r for r in rows[1:] if r and r[0]]

    def col(name: str) -> int | None:
        return header.index(name) if name in header else None

    def truthy(row: list, index: int | None) -> bool:
        return index is not None and index < len(row) and str(row[index]).upper() == "TRUE"

    def filled(row: list, index: int | None) -> bool:
        return index is not None and index < len(row) and bool(str(row[index]).strip())

    stamp = dt.date.today().strftime("%d.%m")
    comment = col("Комментарий системы")
    return {
        "objects": len(body),
        "approved": sum(truthy(r, col("✅ Одобрено")) for r in body),
        "requested": sum(filled(r, col("Запрошено")) for r in body),
        "pdf": sum(filled(r, col("Презентация")) for r in body),
        "new_today": sum(1 for r in body
                         if comment is not None and comment < len(r) and str(r[comment]).startswith(f"🆕 {stamp}")),
    }


def update_summary(client: Client, searches: list[Search], stats: dict[str, dict]) -> None:
    """Перезаписать лист «Сводка»: активные сверху, закрытые внизу."""
    sid = client.spreadsheet_id
    ordered = sorted(searches, key=lambda s: (s.status == "closed", s.created))
    rows = [SUMMARY_HEADERS]
    for s in ordered:
        st = stats.get(s.slug, {})
        rows.append([s.title, s.icon, _status_label(s), st.get("objects", ""), st.get("new_today", ""),
                     st.get("approved", ""), st.get("requested", ""), st.get("pdf", ""),
                     st.get("last_run", ""), s.title if s.status != "closed" else f"✕ {s.title}"])
    sheets.clear_range(sid, f"{SUMMARY_SHEET}!A1:Z200")
    sheets.update_range(sid, f"{SUMMARY_SHEET}!A1", rows)


def append_log(client: Client, lines: list[tuple[str, str]], today: dt.date | None = None) -> None:
    """Строки (подбор, событие) в лист «Лог»."""
    if not lines:
        return
    stamp = (today or dt.date.today()).strftime("%d.%m.%Y")
    sheets.create_sheet(client.spreadsheet_id, LOG_SHEET, rows=2000, columns=3)
    sheets.append_rows(client.spreadsheet_id, f"{LOG_SHEET}!A:C",
                       [[stamp, search, event] for search, event in lines])


def _status_label(search: Search) -> str:
    if search.status == "closed":
        return {"bought": "✓ куплено", "replaced": "✕ заменён", "dropped": "✕ закрыт"}.get(
            search.close_reason, "✕ закрыт")
    return {"active": "активен", "paused": "⏸ пауза"}.get(search.status, search.status)


# ------------------------------------------------------------------ панель

def ensure_panel() -> str:
    """Книга «Панель» в рабочей папке: одна на всех клиентов."""
    found = drive.find_spreadsheet(PANEL_TITLE, working_folder_id())
    if found:
        return found["id"]
    created = drive.create_spreadsheet_in_folder(PANEL_TITLE, working_folder_id())
    sid = created["id"]
    sheets.create_sheet(sid, PANEL_SHEET, rows=500, columns=len(PANEL_HEADERS) + 2)
    sheets.update_range(sid, f"{PANEL_SHEET}!A1", [PANEL_HEADERS])
    for sheet in sheets.list_sheets(sid):
        if sheet["title"] in ("Sheet1", "Лист1"):
            sheets.delete_sheet(sid, sheet["sheet_id"])
    return sid


def write_panel(panel_id: str, rows: list[list]) -> None:
    sheets.clear_range(panel_id, f"{PANEL_SHEET}!A1:Z500")
    sheets.update_range(panel_id, f"{PANEL_SHEET}!A1", [PANEL_HEADERS] + rows)


def panel_row(client: Client, search: Search, stats: dict, topic_link: str = "") -> list:
    return [client.name, search.title, search.icon, _status_label(search),
            stats.get("objects", ""), stats.get("approved", ""), stats.get("requested", ""),
            stats.get("pdf", ""), stats.get("last_run", ""), client.deadline,
            sheet_url(client.spreadsheet_id), topic_link]


__all__ = [
    "SECONDARY_HEADERS", "OWNER_COLUMNS", "MARKET_SECONDARY", "MARKET_OFFPLAN",
    "create_client_book", "create_search_sheet", "close_search_sheet", "search_stats",
    "update_summary", "append_log", "ensure_panel", "write_panel", "panel_row", "sheet_url",
]
