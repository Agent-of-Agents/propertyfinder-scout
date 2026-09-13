"""Запросы брокерам по галочкам в таблице.

Алексей отмечает в колонке «📩 Запросить» объекты, по которым хочет связаться.
Скрипт собирает для них свежие WhatsApp-ссылки Property Finder и складывает
в одну страницу: открыл на телефоне, прошёл кнопками сверху вниз.

    .venv\\Scripts\\python.exe scripts\\send_requests.py
    .venv\\Scripts\\python.exe scripts\\send_requests.py --dry-run

Отправляет человек. Программно слать с номера, привязанного к приложению
WhatsApp Business, нельзя — подробности в lib/whatsapp.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import sheets, whatsapp  # noqa: E402

# ID книги клиента — из окружения или аргумента --spreadsheet, в коде не храним.
SPREADSHEET_ID = os.environ.get("PODBOR_SPREADSHEET_ID", "")
SHEET_TITLE = "Объекты"

REQUEST_COLUMN = "📩 Запросить"
SENT_COLUMN = "Запрошено"
ANCHOR_COLUMN = "✅ Одобрено"      # новые колонки встают сразу за ней


def column_letter(index: int) -> str:
    """Индекс колонки от нуля → буква A1-нотации."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def is_checked(value) -> bool:
    return str(value).strip().upper() in {"TRUE", "ИСТИНА", "1", "ДА"}


def ensure_columns(spreadsheet_id: str, sheet: dict, headers: list[str]) -> bool:
    """Добавить свои колонки, если их ещё нет. True — таблица изменилась."""
    if REQUEST_COLUMN in headers and SENT_COLUMN in headers:
        return False

    if ANCHOR_COLUMN not in headers:
        raise SystemExit(
            f"В листе «{sheet['title']}» нет колонки «{ANCHOR_COLUMN}» — "
            "непонятно, куда вставлять. Проверьте разметку таблицы."
        )

    at = headers.index(ANCHOR_COLUMN) + 1
    sheets.insert_columns(spreadsheet_id, sheet["sheet_id"], at=at, count=2)
    sheets.update_range(
        spreadsheet_id,
        f"{sheet['title']}!{column_letter(at)}1",
        [[REQUEST_COLUMN, SENT_COLUMN]],
    )
    sheets.set_checkbox_column(
        spreadsheet_id, sheet["sheet_id"], column_index=at,
        first_row=1, last_row=sheet.get("rows") or 1000,
    )
    print(f"Добавлены колонки «{REQUEST_COLUMN}» и «{SENT_COLUMN}»")
    return True


def read_table(spreadsheet_id: str, title: str) -> tuple[list[str], list[list]]:
    values = sheets.read_range(spreadsheet_id, f"{title}!A1:AZ")
    if not values:
        raise SystemExit(f"Лист «{title}» пуст")
    return [str(h).strip() for h in values[0]], values[1:]


def pick(row: list, headers: list[str], name: str, default: str = "") -> str:
    if name not in headers:
        return default
    index = headers.index(name)
    return str(row[index]).strip() if index < len(row) else default


def main() -> int:
    # Консоль Windows по умолчанию не печатает эмодзи из названий колонок
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Запросы брокерам по галочкам в таблице")
    parser.add_argument("--spreadsheet", default=SPREADSHEET_ID)
    parser.add_argument("--sheet", default=SHEET_TITLE)
    parser.add_argument("--out", default="whatsapp_requests.html",
                        help="куда сохранить страницу")
    parser.add_argument("--limit", type=int, default=0,
                        help="взять не больше стольких объектов")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать список, ничего не писать в таблицу")
    parser.add_argument("--prepare", metavar="FILE",
                        help="шаг 1: записать идентификаторы объектов для браузера")
    parser.add_argument("--links", metavar="FILE",
                        help="шаг 3: взять ссылки из файла, заполненного браузером")
    args = parser.parse_args()

    sheet = sheets.find_sheet(args.spreadsheet, args.sheet)
    if not sheet:
        raise SystemExit(f"Лист «{args.sheet}» не найден")

    headers, rows = read_table(args.spreadsheet, args.sheet)
    if ensure_columns(args.spreadsheet, sheet, headers):
        headers, rows = read_table(args.spreadsheet, args.sheet)

    if REQUEST_COLUMN not in headers:
        raise SystemExit(f"Колонка «{REQUEST_COLUMN}» не появилась — проверьте таблицу")

    request_index = headers.index(REQUEST_COLUMN)
    sent_index = headers.index(SENT_COLUMN)

    selected = []
    for offset, row in enumerate(rows):
        if request_index >= len(row) or not is_checked(row[request_index]):
            continue
        selected.append({
            "sheet_row": offset + 2,          # +1 шапка, +1 нумерация с единицы
            "id": pick(row, headers, "listing_id"),
            "url": pick(row, headers, "Ссылка"),
            "price": pick(row, headers, "Цена AED"),
            "delta": pick(row, headers, "К рынку"),
            "rooms": pick(row, headers, "Тип"),
            "size_m2": pick(row, headers, "Площадь м²"),
            "agent": pick(row, headers, "Брокер"),
            "agency": pick(row, headers, "Агентство"),
            "agency_phone": pick(row, headers, "Телефон"),
            "dld_phone": pick(row, headers, "Тел. брокера DLD"),
            "dld_name": pick(row, headers, "Брокер"),
            "ru": pick(row, headers, "🇷🇺") == "🇷🇺",
            "was_sent": pick(row, headers, SENT_COLUMN),
        })

    if not selected:
        print(f"Ни одной галочки в колонке «{REQUEST_COLUMN}» — отмечать нечего")
        return 0

    if args.limit:
        selected = selected[: args.limit]

    already = [item for item in selected if item["was_sent"]]
    if already:
        print(f"Внимание: по {len(already)} объектам запрос уже отправляли "
              f"({', '.join(i['id'] for i in already[:5])}) — они снова в списке")

    if args.prepare:
        pending = []
        for item in selected:
            try:
                identity = whatsapp.listing_identity(item["url"])
            except Exception as error:  # noqa: BLE001
                print(f"  {item['id']}: {error}")
                continue
            pending.append({"id": item["id"], "url": item["url"], **identity, "whatsapp_link": ""})
        Path(args.prepare).write_text(
            json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Идентификаторы {len(pending)} объектов записаны в {args.prepare} — "
              "теперь ссылки запрашивает браузер")
        return 0

    links = None
    if args.links:
        pending = json.loads(Path(args.links).read_text(encoding="utf-8"))
        links = {p["id"]: p.get("whatsapp_link") or "" for p in pending}
        print(f"Ссылок из браузера: {sum(1 for v in links.values() if v)} из {len(links)}")

    print(f"Отмечено объектов: {len(selected)}. Собираю ссылки…")
    collected = whatsapp.collect_links(selected, links=links)

    for item in collected:
        # Цена из таблицы приходит строкой с разделителями — вернём число
        digits = re.sub(r"[^\d]", "", str(item.get("price") or ""))
        item["price"] = int(digits) if digits else None

    ready = [item for item in collected if item.get("wa_link")]
    failed = [item for item in collected if not item.get("wa_link")]

    batch = f"{args.sheet}-{dt.date.today().isoformat()}"
    built = dt.datetime.now().strftime("собрано %d.%m %H:%M")
    page = whatsapp.build_page(collected, batch=batch, built=built)

    out = Path(args.out)
    out.write_text(page, encoding="utf-8")

    print(f"Ссылок готово: {len(ready)} из {len(collected)}")
    for item in failed:
        print(f"  без ссылки {item['id']}: {item.get('wa_error')}")
    print(f"Страница: {out.resolve()}")

    if args.dry_run:
        print("--dry-run: таблица не тронута")
        return 0

    # Дата — след того, что запрос подготовлен; галочку снимаем, чтобы
    # следующий запуск не собрал те же объекты заново.
    stamp = dt.date.today().strftime("%d.%m.%Y")
    updates = {}
    for item in ready:
        row = item["sheet_row"]
        updates[f"{args.sheet}!{column_letter(request_index)}{row}"] = [[False]]
        updates[f"{args.sheet}!{column_letter(sent_index)}{row}"] = [[stamp]]

    if updates:
        sheets.update_ranges(args.spreadsheet, updates)
        print(f"В таблице отмечено «{SENT_COLUMN} = {stamp}» по {len(ready)} строкам")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
