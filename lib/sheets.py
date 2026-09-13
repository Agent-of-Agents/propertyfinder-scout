"""Работа с Google Sheets через сервисный аккаунт.

Всё, что умеет модуль:
  - открыть таблицу и получить метаданные      get_spreadsheet
  - получить список листов                     list_sheets
  - прочитать диапазон                         read_range
  - добавить строки в конец                    append_rows
  - изменить существующие значения             update_range
  - создать новый лист внутри книги            create_sheet
  - создать новую таблицу                      create_spreadsheet
  - произвольный batchUpdate                   batch_update

batch_update оставлен наружу сознательно: чекбоксы, цвет строки,
зачёркивание и закрепление шапки делаются только через него.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .google_auth import sheets_service

Row = Sequence[Any]


def spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


# --------------------------------------------------------------------------- чтение


def get_spreadsheet(spreadsheet_id: str, include_grid_data: bool = False) -> dict:
    """Метаданные книги: название, листы, их размеры."""
    return (
        sheets_service()
        .spreadsheets()
        .get(spreadsheetId=spreadsheet_id, includeGridData=include_grid_data)
        .execute()
    )


def list_sheets(spreadsheet_id: str) -> list[dict]:
    """Список листов книги: title, sheetId, число строк и колонок."""
    meta = get_spreadsheet(spreadsheet_id)
    sheets = []
    for sheet in meta.get("sheets", []):
        props = sheet["properties"]
        grid = props.get("gridProperties", {})
        sheets.append(
            {
                "title": props["title"],
                "sheet_id": props["sheetId"],
                "index": props.get("index", 0),
                "rows": grid.get("rowCount"),
                "columns": grid.get("columnCount"),
            }
        )
    return sheets


def read_range(spreadsheet_id: str, a1_range: str) -> list[list[Any]]:
    """Значения диапазона. Пустой диапазон возвращает []."""
    result = (
        sheets_service()
        .spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=a1_range)
        .execute()
    )
    return result.get("values", [])


def read_ranges(spreadsheet_id: str, a1_ranges: Iterable[str]) -> dict[str, list[list[Any]]]:
    """Несколько диапазонов одним запросом — экономит квоту при ежедневном синке."""
    ranges = list(a1_ranges)
    result = (
        sheets_service()
        .spreadsheets()
        .values()
        .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges)
        .execute()
    )
    return {
        value_range.get("range", ranges[i]): value_range.get("values", [])
        for i, value_range in enumerate(result.get("valueRanges", []))
    }


# --------------------------------------------------------------------------- запись


def update_range(
    spreadsheet_id: str,
    a1_range: str,
    values: Sequence[Row],
    raw: bool = False,
) -> dict:
    """Перезаписать конкретный диапазон.

    raw=False — Google сам разберёт числа, даты и формулы (обычный случай).
    raw=True  — записать ровно как есть, без интерпретации.
    """
    return (
        sheets_service()
        .spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=a1_range,
            valueInputOption="RAW" if raw else "USER_ENTERED",
            body={"values": [list(row) for row in values]},
        )
        .execute()
    )


def append_rows(
    spreadsheet_id: str,
    a1_range: str,
    values: Sequence[Row],
    raw: bool = False,
) -> dict:
    """Дописать строки в конец таблицы. a1_range задаёт лист, например 'Объекты!A:Z'."""
    return (
        sheets_service()
        .spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=a1_range,
            valueInputOption="RAW" if raw else "USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [list(row) for row in values]},
        )
        .execute()
    )


def update_ranges(spreadsheet_id: str, updates: dict[str, Sequence[Row]], raw: bool = False) -> dict:
    """Несколько диапазонов одним запросом.

    Основной способ записи в живой таблице: пишем только свои колонки,
    колонки Алексея остаются нетронутыми.
    """
    data = [
        {"range": a1_range, "values": [list(row) for row in values]}
        for a1_range, values in updates.items()
    ]
    return (
        sheets_service()
        .spreadsheets()
        .values()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW" if raw else "USER_ENTERED", "data": data},
        )
        .execute()
    )


def clear_range(spreadsheet_id: str, a1_range: str) -> dict:
    return (
        sheets_service()
        .spreadsheets()
        .values()
        .clear(spreadsheetId=spreadsheet_id, range=a1_range, body={})
        .execute()
    )


# --------------------------------------------------------------------------- структура


def batch_update(spreadsheet_id: str, requests: Sequence[dict]) -> dict:
    """Произвольные структурные операции: чекбоксы, форматирование, закрепление шапки."""
    return (
        sheets_service()
        .spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": list(requests)})
        .execute()
    )


def create_sheet(spreadsheet_id: str, title: str, rows: int = 1000, columns: int = 40) -> int:
    """Создать лист внутри книги. Возвращает sheetId. Существующий лист не дублируется."""
    for sheet in list_sheets(spreadsheet_id):
        if sheet["title"] == title:
            return sheet["sheet_id"]

    response = batch_update(
        spreadsheet_id,
        [
            {
                "addSheet": {
                    "properties": {
                        "title": title,
                        "gridProperties": {"rowCount": rows, "columnCount": columns},
                    }
                }
            }
        ],
    )
    return response["replies"][0]["addSheet"]["properties"]["sheetId"]


def delete_sheet(spreadsheet_id: str, sheet_id: int) -> dict:
    return batch_update(spreadsheet_id, [{"deleteSheet": {"sheetId": sheet_id}}])


def create_spreadsheet(title: str, sheet_titles: Sequence[str] | None = None) -> str:
    """Создать новую таблицу через Sheets API. Возвращает spreadsheetId.

    Внимание: файл окажется на Диске сервисного аккаунта, у которого нет
    собственной квоты хранилища. Для рабочих подборок используй
    drive.create_spreadsheet_in_folder — она кладёт файл в папку Алексея.
    """
    body: dict = {"properties": {"title": title}}
    if sheet_titles:
        body["sheets"] = [{"properties": {"title": t}} for t in sheet_titles]

    result = sheets_service().spreadsheets().create(body=body, fields="spreadsheetId").execute()
    return result["spreadsheetId"]


# --------------------------------------------------------------------------- готовые приёмы


def set_checkbox_column(spreadsheet_id: str, sheet_id: int, column_index: int,
                        first_row: int = 1, last_row: int = 1000) -> dict:
    """Превратить колонку в настоящие чекбоксы.

    column_index и first_row отсчитываются от нуля; first_row=1 пропускает шапку.
    """
    return batch_update(
        spreadsheet_id,
        [
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": first_row,
                        "endRowIndex": last_row,
                        "startColumnIndex": column_index,
                        "endColumnIndex": column_index + 1,
                    },
                    "rule": {"condition": {"type": "BOOLEAN"}, "strict": True},
                }
            }
        ],
    )


def freeze_header(spreadsheet_id: str, sheet_id: int, rows: int = 1, columns: int = 0) -> dict:
    return batch_update(
        spreadsheet_id,
        [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": rows, "frozenColumnCount": columns},
                    },
                    "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
                }
            }
        ],
    )


def insert_columns(spreadsheet_id: str, sheet_id: int, at: int, count: int = 1) -> dict:
    """Вставить пустые колонки перед колонкой с индексом at (от нуля).

    Данные справа сдвигаются, формулы и ссылки Google Sheets переносит сам.
    """
    return batch_update(
        spreadsheet_id,
        [
            {
                "insertDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": at,
                        "endIndex": at + count,
                    },
                    "inheritFromBefore": False,
                }
            }
        ],
    )


def find_sheet(spreadsheet_id: str, title: str) -> dict | None:
    """Лист по имени или None."""
    for sheet in list_sheets(spreadsheet_id):
        if sheet["title"] == title:
            return sheet
    return None
