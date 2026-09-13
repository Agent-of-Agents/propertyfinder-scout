"""DLD-проверка объектов: точная площадь, личный номер агента, дубли, номер юнита.

Два шага, потому что карту DLD читает браузер (reCAPTCHA v3), а не скрипт:

    dld_lookup.py --queue            → data/dld_queue.json: какие строки пробить и их ссылки
    dld_lookup.py --ingest FILE      → разобрать тексты карт, записать в таблицу

FILE — JSON {listing_id: "текст страницы Trakheesi"} либо {listing_id: {…поля…}}.

Что пишется в таблицу (свои колонки, добавляются в конец шапки, если их нет):
    DLD м²  · Тел. брокера DLD · Email DLD · Разрешение до · Юнит DLD · Дубли

Какие строки в очереди: одобренные (✅), запрошенные (📩 или дата в «Запрошено»),
и первые N по порядку листа (--top N). Всё сразу не пробиваем — карта DLD
это государственный сервис, дёргаем его ровно столько, сколько нужно.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lib import listing_detail, sheets, sync, trakheesi  # noqa: E402

DLD_COLUMNS = ["DLD м²", "Тел. брокера DLD", "Email DLD", "Разрешение до", "Юнит DLD", "Дубли"]
DUPES_SHEET = "Дубли"
CONFIRM = ROOT / "data" / "dld_dupe_confirm.json"   # {"id|id": "фото 4"} — итог сверки фото


def money(value) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return f"{int(digits):,}".replace(",", " ") if digits else ""


def confirmation(a: dict, b: dict, key_pair: str, confirm: dict) -> str:
    """Насколько уверенно это одна квартира, а не соседняя по стояку."""
    if a.get("permit_number") and a.get("permit_number") == b.get("permit_number"):
        return "одно разрешение DLD — точно одна квартира"
    note = confirm.get(key_pair)
    if note:
        return note
    return "одна планировка; фото разные — уточнить у брокера"
QUEUE = ROOT / "data" / "dld_queue.json"
CARDS = ROOT / "data" / "dld_cards.json"          # накопленные карты, чтобы не пробивать дважды


def load_client(name_part: str) -> tuple[Path, dict]:
    for cfg in sorted((ROOT / "clients").glob("*/client.json")):
        if name_part.lower() in cfg.parent.name.lower():
            return cfg.parent, json.loads(cfg.read_text(encoding="utf-8"))
    raise SystemExit(f"Клиент «{name_part}» не найден")


def ensure_columns(view: sync.SheetView) -> None:
    missing = [c for c in DLD_COLUMNS if c not in view.col]
    if not missing:
        return
    start = len(view.header)
    # На листе может не хватать столбцов — добавляем физически, иначе запись за Z молча теряется
    meta = {s["title"]: s for s in sheets.list_sheets(view.spreadsheet_id)}[view.title]
    need = start + len(missing) - (meta.get("columns") or 26)
    if need > 0:
        sheets.batch_update(view.spreadsheet_id, [{"appendDimension": {
            "sheetId": view.sheet_id, "dimension": "COLUMNS", "length": need}}])
    sheets.update_range(view.spreadsheet_id,
                        f"{view.title}!{sync._letter(start)}1", [missing])
    print(f"Добавлены колонки: {', '.join(missing)}")
    view.reload()


def truthy(value: str) -> bool:
    return str(value).strip().upper() in {"TRUE", "ИСТИНА", "1"}


def build_queue(client: dict, top: int) -> list[dict]:
    view = sync.SheetView(client["spreadsheet_id"], client.get("sheet", "Объекты"))
    known = json.loads(CARDS.read_text(encoding="utf-8")) if CARDS.exists() else {}
    queue = []
    for offset, row in enumerate(view.rows):
        listing_id = view.cell(row, "listing_id")
        if not listing_id:
            continue
        wanted = (truthy(view.cell(row, "✅ Одобрено"))
                  or truthy(view.cell(row, "📩 Запросить"))
                  or bool(view.cell(row, "Запрошено"))
                  or offset < top)
        if not wanted or listing_id in known:
            continue
        url = view.cell(row, "Ссылка")
        if not url:
            continue
        try:
            detail = listing_detail.fetch_detail(url)
        except Exception as error:  # noqa: BLE001
            print(f"  {listing_id}: страница не открылась — {str(error)[:80]}")
            continue
        queue.append({
            "listing_id": listing_id,
            "agent": view.cell(row, "Брокер"),
            "price": view.cell(row, "Цена AED"),
            "validation_url": detail.get("permit_validation_url", ""),
            "plan_units": detail.get("plan_unit_numbers", []),
        })
    return queue


def ingest(client: dict, results: dict) -> None:
    view = sync.SheetView(client["spreadsheet_id"], client.get("sheet", "Объекты"))
    ensure_columns(view)

    cards = json.loads(CARDS.read_text(encoding="utf-8")) if CARDS.exists() else {}
    for listing_id, payload in results.items():
        card = trakheesi.parse_card(payload) if isinstance(payload, str) else dict(payload)
        if card:
            cards[listing_id] = card
    CARDS.parent.mkdir(exist_ok=True)
    CARDS.write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")

    # дубли — по всем накопленным картам, не только по свежим
    on_sheet = {view.cell(r, "listing_id") for r in view.rows}
    duplicates = trakheesi.find_duplicates({k: v for k, v in cards.items() if k in on_sheet})

    confirm = json.loads(CONFIRM.read_text(encoding="utf-8")) if CONFIRM.exists() else {}

    def row_info(lid: str) -> dict:
        n = view.row_of(lid)
        r = view.rows[n - 2] if n else []
        return {"price": view.cell(r, "Цена AED"), "agent": view.cell(r, "Брокер"),
                "agency": view.cell(r, "Агентство"), "url": view.cell(r, "Ссылка"),
                "type": view.cell(r, "Тип")}

    def dupe_text(lid: str) -> str:
        """Конкуренты по этой квартире: цена · брокер · ссылка, по строке на каждого."""
        lines = []
        for other in sorted(duplicates.get(lid, []), key=lambda o: money(row_info(o)["price"]) or "9"):
            info = row_info(other)
            pair = "|".join(sorted([lid, other]))
            sure = confirmation(cards.get(lid, {}), cards.get(other, {}), pair, confirm)
            mark = "✔" if "точно" in sure or "фото" in sure and "разные" not in sure else "≈"
            lines.append(f"{mark} {money(info['price'])} AED · {info['agent']} · {info['url']}")
        return "\n".join(lines)

    updates = {}
    for listing_id, card in cards.items():
        row_number = view.row_of(listing_id)
        if not row_number or not card.get("verified"):
            continue
        size = card.get("size_sqm")
        units = trakheesi.match_unit(card.get("building", ""), size)
        values = {
            "DLD м²": size or "",
            "Тел. брокера DLD": card.get("broker_mobile", ""),
            "Email DLD": card.get("broker_email", ""),
            "Разрешение до": card.get("permit_until", ""),
            "Юнит DLD": trakheesi.describe_units(units) if units else (
                "реестра здания нет" if size else ""),
            "Дубли": dupe_text(listing_id),
        }
        for name, value in values.items():
            view.guard(name)
            updates[view.a1(row_number, name)] = [[value]]
    if updates:
        sheets.update_ranges(view.spreadsheet_id, updates)

    write_dupes_sheet(view, cards, duplicates, confirm, row_info)

    verified = sum(1 for c in cards.values() if c.get("verified"))
    with_phone = sum(1 for c in cards.values() if c.get("broker_mobile"))
    print(f"Карт всего: {len(cards)}, верифицированных: {verified}, "
          f"с личным номером агента: {with_phone}, пар дублей: {len(duplicates)}")
    for listing_id, others in duplicates.items():
        print(f"  дубль: {listing_id} ↔ {', '.join(others)}")


def write_dupes_sheet(view, cards: dict, duplicates: dict, confirm: dict, row_info) -> None:
    """Отдельный лист: группа = одна квартира, строка = объявление конкурента."""
    groups: dict[str, list[str]] = {}
    for lid in duplicates:
        key = trakheesi.fingerprint(cards[lid]["building"], cards[lid]["size_sqm"])
        groups.setdefault(key, []).append(lid)

    header = ["Группа", "Тип", "DLD м²", "Цена AED", "Брокер", "Агентство",
              "Тел. брокера DLD", "Ссылка", "Уверенность", "listing_id"]
    values = [header]
    for index, (key, members) in enumerate(sorted(groups.items()), start=1):
        members = sorted(set(members), key=lambda m: money(row_info(m)["price"]) or "9")
        sqm = key.split("|")[1]
        for lid in members:
            info = row_info(lid)
            others = [o for o in members if o != lid]
            sure = confirmation(cards[lid], cards[others[0]], "|".join(sorted([lid, others[0]])), confirm) if others else ""
            values.append([f"№{index}", info["type"], sqm, money(info["price"]), info["agent"],
                           info["agency"], cards[lid].get("broker_mobile", ""), info["url"], sure, lid])
        values.append([""] * len(header))

    sheet_id = sheets.create_sheet(view.spreadsheet_id, DUPES_SHEET, rows=500, columns=12)
    sheets.clear_range(view.spreadsheet_id, f"{DUPES_SHEET}!A1:L500")
    sheets.update_range(view.spreadsheet_id, f"{DUPES_SHEET}!A1", values)
    sheets.freeze_header(view.spreadsheet_id, sheet_id)
    print(f"Лист «{DUPES_SHEET}»: {len(groups)} групп, {sum(len(v) for v in groups.values())} объявлений")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True, help="slug папки клиента в clients/")
    parser.add_argument("--queue", action="store_true")
    parser.add_argument("--top", type=int, default=0, help="плюс первые N строк листа")
    parser.add_argument("--ingest", metavar="FILE")
    args = parser.parse_args()

    _, client = load_client(args.client)

    if args.queue:
        queue = build_queue(client, args.top)
        QUEUE.parent.mkdir(exist_ok=True)
        QUEUE.write_text(json.dumps(queue, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"В очереди {len(queue)} объектов → {QUEUE}")
        for item in queue:
            print(f"  {item['listing_id']}  {item['agent']}  {item['price']}")
        return 0

    if args.ingest:
        ingest(client, json.loads(Path(args.ingest).read_text(encoding="utf-8")))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
