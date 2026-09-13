"""Подбор off-plan под клиента → лист «Off-plan» в его книге.

    .venv\\Scripts\\python.exe scripts\\offplan_pick.py --client ivanov
    .venv\\Scripts\\python.exe scripts\\offplan_pick.py --client ivanov --collect     (сначала обновить рынок)
    .venv\\Scripts\\python.exe scripts\\offplan_pick.py --client ivanov --preview     (не писать, показать топ)

Разовый подбор без папки клиента — бриф прямо из аргументов:

    .venv\\Scripts\\python.exe scripts\\offplan_pick.py --bedrooms 1,2 --max 3000000 ^
        --community "Dubai Marina,Business Bay" --delivery-by 2029-12-31 --preview

Бриф клиента — блок "offplan" в clients/<папка>/client.json (см. lib/offplan.py).
Если в client.json нет spreadsheet_id, книга создаётся на Общем диске.
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

from lib import drive, offplan, offplan_sheet, pf_projects, sheets  # noqa: E402

CLIENTS_DIR = ROOT / "clients"


def money(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def find_client(name: str) -> tuple[Path, dict]:
    for cfg in sorted(CLIENTS_DIR.glob("*/client.json")):
        if name.lower() in cfg.parent.name.lower():
            return cfg.parent, json.loads(cfg.read_text(encoding="utf-8"))
    raise SystemExit(f"Папка клиента с «{name}» не найдена в clients/")


def brief_from_args(args) -> dict:
    brief: dict = {}
    if args.bedrooms:
        brief["bedrooms"] = [b.strip() for b in args.bedrooms.split(",")]
    if args.max or args.min:
        brief["budget"] = {k: v for k, v in (("max", args.max), ("min", args.min)) if v}
    if args.community:
        brief["communities"] = [c.strip() for c in args.community.split(",")]
    if args.developer:
        brief["developers"] = [d.strip() for d in args.developer.split(",")]
    if args.delivery_by:
        brief["delivery_by"] = args.delivery_by
    if args.launches_only:
        brief["launches_only"] = True
    if args.post_handover:
        brief["post_handover"] = True
    if args.max_down_payment:
        brief["max_down_payment"] = args.max_down_payment
    return brief


def ensure_spreadsheet(client_dir: Path, client: dict) -> str:
    """ID книги клиента; если нет — создать на Общем диске и записать в client.json."""
    if client.get("spreadsheet_id"):
        return client["spreadsheet_id"]
    title = f"{client['name']} — Off-plan"
    created = drive.create_spreadsheet_in_folder(title)
    client["spreadsheet_id"] = created["id"]
    (client_dir / "client.json").write_text(json.dumps(client, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Создана книга «{title}»: {sheets.spreadsheet_url(created['id'])}")
    return created["id"]


def print_preview(rows: list[dict], limit: int) -> None:
    print(f"\nПодошло строк: {len(rows)}. Топ-{min(limit, len(rows))}:\n")
    for r in rows[:limit]:
        print(f"{r['Балл']:>3}  {r['Тип']:6} от {money(r['Цена от AED']):>11} AED  "
              f"{r['Площадь м²'] or '—':>6} м²  {r['AED/м²'] or '—':>6}/м²  {r['К району'] or '   ':>5}  "
              f"сдача {r['Сдача'] or '—':8} план {r['План оплаты'][:28]:28}  "
              f"{r['Проект'][:34]} · {r['Застройщик'][:18]} · {r['Район'][:22]}")
        print(f"       {r['Фаза продаж'] or ''} {r['Старт продаж']}  — {r['Комментарий системы']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", help="часть имени папки клиента в clients/")
    parser.add_argument("--collect", action="store_true", help="сначала обновить каталог с PF")
    parser.add_argument("--preview", action="store_true", help="показать подбор, в таблицу не писать")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--bedrooms", help="studio,1,2")
    parser.add_argument("--max", type=int, help="потолок цены «от», AED")
    parser.add_argument("--min", type=int)
    parser.add_argument("--community", help="районы через запятую (подстрока)")
    parser.add_argument("--developer", help="застройщики через запятую (подстрока)")
    parser.add_argument("--delivery-by", help="сдача не позже, ГГГГ-ММ-ДД")
    parser.add_argument("--launches-only", action="store_true")
    parser.add_argument("--post-handover", action="store_true")
    parser.add_argument("--max-down-payment", type=int)
    args = parser.parse_args()

    if args.collect:
        pf_projects.collect()

    client_dir, client = (find_client(args.client) if args.client else (None, None))
    briefs = offplan.client_briefs(client) if client else [{}]
    for brief in briefs:
        brief.update(brief_from_args(args))
    if not any(briefs):
        raise SystemExit("Нет брифа: укажите --client или параметры фильтра")

    projects = pf_projects.load_projects()
    print(f"Каталог: {len(projects)} проектов Дубая.")
    spreadsheet_id = None
    for brief in briefs:
        title = brief.get("sheet", offplan_sheet.SHEET_TITLE)
        rows = offplan.build_rows(brief, projects, dt.date.today())
        shown = {k: v for k, v in brief.items() if k != "sheet"}
        print(f"\n=== Лист «{title}» · бриф: {json.dumps(shown, ensure_ascii=False)}")

        if args.preview or client is None:
            print_preview(rows, args.top)
            continue
        if not rows:
            print("Под бриф не подошло ни одного проекта — в таблицу ничего не пишу")
            continue
        spreadsheet_id = spreadsheet_id or ensure_spreadsheet(client_dir, client)
        report = offplan_sheet.sync(spreadsheet_id, rows, title)
        print(f"Лист «{title}»: новых {len(report['new'])}, изменений {len(report['changed'])}, "
              f"выбыло {len(report['removed'])}, без изменений {report['kept']}")
        for r in report["new"][:10]:
            print(f"  🆕 {r['type']} от {money(r['price'])} — {r['title']}")
        for r in report["changed"][:10]:
            print(f"  {' '.join(r['notes'])} — {r['title']}")
    if spreadsheet_id:
        print("\n" + sheets.spreadsheet_url(spreadsheet_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
