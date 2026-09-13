"""Каталог off-plan Дубая: сбор с Property Finder → книга «Off-plan Дубай» на Общем диске.

    .venv\\Scripts\\python.exe scripts\\offplan_market.py              (собрать и записать)
    .venv\\Scripts\\python.exe scripts\\offplan_market.py --no-collect (только записать из кэша)

Листы книги (перезаписываются целиком, колонок Алексея здесь нет):
  «Запуски»     — проекты до старта продаж и с открытым бронированием
  «Все проекты» — весь каталог застройщиков по Дубаю, одна строка на проект

ID книги хранится в data/pf_projects/catalog.json; если файла нет — книга создаётся.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lib import drive, offplan_sheet, pf_projects as pf, sheets  # noqa: E402
from lib.offplan import LAUNCH_PHASES  # noqa: E402

CATALOG_TITLE = "Off-plan Дубай — каталог PF"
CATALOG_META = pf.DATA_DIR / "catalog.json"

TYPE_COLUMNS = [("studio", "Студия от"), ("1", "1BR от"), ("2", "2BR от"), ("3", "3BR от"), ("4", "4BR от")]
HEADERS = ["Проект", "Застройщик", "Район", "Локация", "Фаза продаж", "Старт продаж", "Сдача", "Стройка",
           "Цена от AED"] + [label for _, label in TYPE_COLUMNS] + [
           "План оплаты", "Взнос %", "После сдачи", "DLD AED/м²", "Сделок DLD", "Интерес PF", "Ссылка", "Брошюра", "Обновлено"]


def ensure_catalog() -> str:
    if CATALOG_META.exists():
        return json.loads(CATALOG_META.read_text(encoding="utf-8"))["spreadsheet_id"]
    found = drive.find_spreadsheet(CATALOG_TITLE)
    spreadsheet_id = found["id"] if found else drive.create_spreadsheet_in_folder(CATALOG_TITLE)["id"]
    CATALOG_META.write_text(json.dumps({"spreadsheet_id": spreadsheet_id}, indent=1), encoding="utf-8")
    return spreadsheet_id


def row_of(project: dict) -> list:
    types = project.get("types") or {}
    plans = project.get("payment_plan_detail") or []
    plan_text = "; ".join(pf.plan_ru(p) for p in plans) or ", ".join(project.get("payment_plans") or [])
    dld = project.get("dld") or {}
    return [
        project.get("title", ""), project.get("developer", ""), project.get("community", ""),
        project.get("location_full", ""), pf.sales_phase_ru(project.get("sales_phase", "")),
        project.get("sales_start", ""), pf.quarter(project.get("delivery_date", "")),
        pf.construction_ru(project.get("construction_phase", ""), project.get("construction_progress")),
        project.get("starting_price") or "",
    ] + [
        (types.get(key) or {}).get("price_from") or "" for key, _ in TYPE_COLUMNS
    ] + [
        plan_text, project.get("down_payment") if project.get("down_payment") is not None else "",
        "да" if project.get("post_handover") else "",
        round(dld["median_ppsf"] / pf.SQFT_TO_M2) if dld.get("median_ppsf") else "",
        dld.get("count") or "", project.get("hotness") or "", project.get("url", ""),
        project.get("brochure_url", ""), (project.get("fetched") or "")[:10],
    ]


def write_sheet(spreadsheet_id: str, title: str, projects: list[dict]) -> None:
    sheet_id = sheets.create_sheet(spreadsheet_id, title, rows=max(2000, len(projects) + 100), columns=len(HEADERS) + 2)
    sheets.clear_range(spreadsheet_id, f"{title}!A1:AZ5000")
    values = [HEADERS] + [row_of(p) for p in projects]
    sheets.update_range(spreadsheet_id, f"{title}!A1", values)
    offplan_sheet.style_sheet(spreadsheet_id, sheet_id, HEADERS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-collect", action="store_true", help="не ходить на PF, взять кэш")
    parser.add_argument("--no-sheet", action="store_true", help="только собрать, в книгу не писать")
    args = parser.parse_args()

    projects = pf.load_projects() if args.no_collect else pf.collect()
    launches = [p for p in projects if p.get("sales_phase") in LAUNCH_PHASES]
    launches.sort(key=lambda p: (p.get("sales_start") or "", p.get("hotness") or 0), reverse=True)
    projects_sorted = sorted(projects, key=lambda p: (p.get("developer", ""), p.get("title", "")))

    print(f"Проектов: {len(projects)}, из них на запуске: {len(launches)}")
    if args.no_sheet:
        return 0

    spreadsheet_id = ensure_catalog()
    write_sheet(spreadsheet_id, "Запуски", launches)
    write_sheet(spreadsheet_id, "Все проекты", projects_sorted)
    # Пустой лист по умолчанию больше не нужен
    for sheet in sheets.list_sheets(spreadsheet_id):
        if sheet["title"] in ("Sheet1", "Лист1"):
            sheets.delete_sheet(spreadsheet_id, sheet["sheet_id"])
    print(f"Записано {dt.date.today():%d.%m.%Y}: {sheets.spreadsheet_url(spreadsheet_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
