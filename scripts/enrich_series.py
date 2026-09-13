"""Колонка «Серия» в листе «Объекты»: серия / тип планировки из текста объявления.

Серия видна только в описании, а описание есть только на странице объявления —
поэтому карточки качаются по одной и кэшируются в raw/details.json. Повторно
не качаются: у объявления, которое уже проверяли, серия не появится.

    enrich_series.py --client ivanov
    из daily.py — enrich(client_dir, client) после сверки
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lib import layout, listing_detail, sheets, sync  # noqa: E402

COLUMN = "Серия"


def enrich(client_dir: Path, client: dict, pause: float = 0.7) -> dict:
    view = sync.SheetView(client["spreadsheet_id"], client.get("sheet", "Объекты"))
    if COLUMN not in view.col:
        start = len(view.header)
        meta = {s["title"]: s for s in sheets.list_sheets(view.spreadsheet_id)}[view.title]
        if start + 1 > (meta.get("columns") or 26):
            sheets.batch_update(view.spreadsheet_id, [{"appendDimension": {
                "sheetId": view.sheet_id, "dimension": "COLUMNS", "length": 1}}])
        sheets.update_range(view.spreadsheet_id, f"{view.title}!{sync._letter(start)}1", [[COLUMN]])
        view.reload()

    cache_path = client_dir / "raw" / "details.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    updates, found, fetched = {}, 0, 0
    for offset, row in enumerate(view.rows):
        lid = view.cell(row, "listing_id")
        if not lid or view.cell(row, COLUMN):
            continue
        entry = cache.get(lid)
        if entry is None:
            url = view.cell(row, "Ссылка")
            if not url:
                continue
            try:
                d = listing_detail.fetch_detail(url)
                entry = {"title": d["title"],
                         "description": re.sub(r"<[^>]+>", " ", d["description"]),
                         "plans": len(d["floor_plans"]), "plan_units": d["plan_unit_numbers"]}
            except Exception as error:  # noqa: BLE001
                entry = {"error": str(error)[:80]}
            cache[lid] = entry
            fetched += 1
            time.sleep(pause)
        if "error" in entry:
            continue
        info = layout.extract(entry.get("title", ""), entry.get("description", ""))
        if entry.get("plan_units"):
            info["unit"] = entry["plan_units"][0]
        text = layout.label(info) or "—"
        if text != "—":
            found += 1
        view.guard(COLUMN)
        updates[view.a1(offset + 2, COLUMN)] = [[text]]

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    if updates:
        sheets.update_ranges(view.spreadsheet_id, updates)
    return {"checked": len(updates), "found": found, "fetched": fetched}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True, help="slug папки клиента в clients/")
    args = parser.parse_args()
    for cfg in sorted((ROOT / "clients").glob("*/client.json")):
        if args.client.lower() in cfg.parent.name.lower():
            client = json.loads(cfg.read_text(encoding="utf-8"))
            r = enrich(cfg.parent, client)
            print(f"проверено {r['checked']}, серия найдена у {r['found']}, скачано карточек {r['fetched']}")
            return 0
    raise SystemExit("клиент не найден")


if __name__ == "__main__":
    raise SystemExit(main())
