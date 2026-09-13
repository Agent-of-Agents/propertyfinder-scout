"""Ежедневный прогон по всем активным клиентам.

Запускается Планировщиком Windows в 09:00 МСК, можно и руками:

    .venv\\Scripts\\python.exe scripts\\daily.py
    .venv\\Scripts\\python.exe scripts\\daily.py --client ivanov   (только один)
    .venv\\Scripts\\python.exe scripts\\daily.py --dry-run           (собрать, но не писать)

Что делает для каждого клиента с status=active в clients/*/client.json:
  1. собирает рынок заново по источникам из client.json
  2. сверяет с таблицей: новые сверху с заливкой, изменения цен, снятые
  3. дистресс-источники → лист «Дистресс», совпадения с брифом
  4. галочки ✅ с ценой → презентации на Диск
  5. блок "offplan" в брифе → листы off-plan из каталога PF (каталог
     обновляется один раз за прогон, до обхода клиентов); галочки ✅ на
     этих листах → презентации на Диск
  6. отчёт: лист «Лог» в книге клиента, log.md в папке, logs/daily.log

Ничего не удаляет, в колонки Алексея не пишет, при несовпадении шапки
останавливается до первой записи.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lib import distress, offplan, offplan_sheet, pf_projects, propertyfinder, sheets, sync  # noqa: E402
from scripts import make_presentation as mp  # noqa: E402

CLIENTS_DIR = ROOT / "clients"
LOG_DIR = ROOT / "logs"


def money(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def active_clients(only: str | None = None) -> list[tuple[Path, dict]]:
    found = []
    for cfg in sorted(CLIENTS_DIR.glob("*/client.json")):
        client = json.loads(cfg.read_text(encoding="utf-8"))
        if client.get("status") != "active":
            continue
        if only and only.lower() not in cfg.parent.name.lower():
            continue
        found.append((cfg.parent, client))
    return found


def collect(client: dict) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for source in client.get("sources", []):
        if source["kind"] != "propertyfinder":
            continue
        for row in propertyfinder.collect(source["url"], max_pages=8):
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)
    return rows


def write_log_sheet(client: dict, today: dt.date, lines: list[str]) -> None:
    sid = client["spreadsheet_id"]
    sheets.create_sheet(sid, "Лог", rows=2000, columns=3)
    existing = sheets.read_range(sid, "Лог!A1:A1")
    if not existing:
        sheets.update_range(sid, "Лог!A1", [["Дата", "Событие", "Детали"]])
    stamp = today.strftime("%d.%m.%Y")
    sheets.append_rows(sid, "Лог!A:C", [[stamp, line.split(":", 1)[0], line.split(":", 1)[-1].strip()]
                                        for line in lines])


def run_client(client_dir: Path, client: dict, today: dt.date, dry_run: bool) -> list[str]:
    name = client["name"]
    lines: list[str] = []
    print(f"\n{'═' * 60}\n{name} — {client.get('target', '')}\n{'═' * 60}")

    if client.get("sources"):
        lines += run_listings(client_dir, client, today, dry_run)
    if client.get("offplan"):
        lines += run_offplan(client_dir, client, today, dry_run)
    return lines


def run_offplan(client_dir: Path, client: dict, today: dt.date, dry_run: bool) -> list[str]:
    """Лист «Off-plan»: подбор из каталога PF под блок offplan брифа."""
    lines: list[str] = []
    try:
        projects = pf_projects.load_projects()
    except FileNotFoundError as error:
        return [f"off-plan: {error}"]
    for brief in offplan.client_briefs(client):
        title = brief.get("sheet", offplan_sheet.SHEET_TITLE)
        rows = offplan.build_rows(brief, projects, today)
        if dry_run or not client.get("spreadsheet_id"):
            lines.append(f"{title}: подошло {len(rows)} строк из {len(projects)} проектов"
                         + (" (dry-run)" if dry_run else " — нет spreadsheet_id, лист не пишу"))
            continue
        report = offplan_sheet.sync(client["spreadsheet_id"], rows, title, today)
        lines.append(f"{title}: подошло {len(rows)} строк; новых {len(report['new'])}, "
                     f"изменений {len(report['changed'])}, выбыло {len(report['removed'])}")
        for r in report["new"][:8]:
            lines.append(f"{title} НОВЫЙ: {r['type']} от {money(r['price'])} — {r['title']}")
        for r in report["changed"][:8]:
            lines.append(f"{title}: {' '.join(r['notes'])} — {r['title']}")
    # галочки → презентации
    if dry_run:
        return lines
    try:
        from scripts import make_offplan_presentation as mop
        for d in mop.process(client_dir, client, force=False, local=False):
            lines.append(f"презентация off-plan: {d['title']} {d.get('type', '')} — "
                         + (d.get("link") or d["status"]))
    except Exception as error:  # noqa: BLE001
        lines.append(f"презентации off-plan: ошибка — {str(error)[:120]}")
    return lines


def run_listings(client_dir: Path, client: dict, today: dt.date, dry_run: bool) -> list[str]:
    """Объявления PF: сбор, сверка, дистресс, презентации."""
    lines: list[str] = []

    # 1. рынок
    fresh = collect(client)
    print(f"Собрано объявлений: {len(fresh)}")
    if not fresh:
        lines.append("сбор: источник не отдал ни одного объявления — сверку пропускаю")
        return lines
    raw_path = client_dir / client.get("raw", "raw/listings.json")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        raw_path.write_text(json.dumps(fresh, ensure_ascii=False, indent=1), encoding="utf-8")

    if dry_run:
        lines.append(f"сбор: {len(fresh)} объявлений (dry-run, в таблицу не писал)")
        return lines

    # 2. сверка
    report = sync.run(client, fresh, client_dir / "state.json", today)
    med = report["medians"]
    lines.append(f"сбор: {len(fresh)} объявлений; медианы AED/м² — " +
                 ", ".join(f"{k}BR {money(v)}" for k, v in sorted(med.items())))
    if report["new"]:
        lines.append(f"новые: {len(report['new'])} — " + "; ".join(
            f"{r['bedrooms']}BR {money(r['price'])} {r.get('agent_name', '')}" for r in report["new"][:6]))
    for ch in report["price"]:
        lines.append(f"цена: {ch['id']} {money(ch['old'])} → {money(ch['new'])} ({ch.get('agent', '')})")
    for ch in report["title"]:
        lines.append(f"заголовок: {ch['id']} «{ch['old'][:40]}» → «{ch['new'][:40]}»")
    for ch in report["agent"]:
        lines.append(f"агент: {ch['id']} {ch['old']} → {ch['new']}")
    for r in report["removed"]:
        lines.append(f"снято: {r['id']} {r.get('agent', '')} {money(r.get('price'))}")
    for lid in report["approved_removed"]:
        lines.append(f"ВНИМАНИЕ: одобренный объект пропал из выдачи — {lid}")

    # 2b. серия / тип планировки из текста — только для строк, где колонка пуста
    try:
        from scripts import enrich_series
        r = enrich_series.enrich(client_dir, client)
        if r["checked"]:
            lines.append(f"серия: проверено {r['checked']}, найдена у {r['found']}")
    except Exception as error:  # noqa: BLE001
        lines.append(f"серия: ошибка — {str(error)[:100]}")

    # 3. дистресс
    try:
        items = distress.collect_all()
        found = distress.matches(client, items)
        fresh_d = distress.write_new(client, found, today)
        lines.append(f"дистресс: источников прочитано, совпадений с брифом {len(found)}, новых {len(fresh_d)}")
        for it in fresh_d:
            lines.append(f"дистресс НОВЫЙ: {it['project']} {it.get('type', '')} "
                         f"{money(it.get('price'))} {it.get('discount', '')} — {it['source']}")
    except Exception as error:  # noqa: BLE001
        lines.append(f"дистресс: ошибка — {str(error)[:120]}")

    # 4. одобрения → презентации
    try:
        mp.configure(client_dir, client)
        done = mp.process_approved(quiet=True)
        for d in done:
            if d["status"] == "готово":
                lines.append(f"презентация: {d['id']} {d.get('type', '')} → {d.get('link', '')}")
            else:
                lines.append(f"презентация: {d['id']} — {d['status']}")
    except SystemExit as stop:
        lines.append(f"презентации остановлены: {stop}")
    except Exception as error:  # noqa: BLE001
        lines.append(f"презентации: ошибка — {str(error)[:120]}")

    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", help="часть имени папки клиента")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    today = dt.date.today()
    LOG_DIR.mkdir(exist_ok=True)
    started = dt.datetime.now()
    summary: list[str] = [f"Прогон {started:%d.%m.%Y %H:%M}"]

    clients = active_clients(args.client)
    if any(c.get("offplan") for _, c in clients) and not args.dry_run:
        try:
            pf_projects.collect(progress=lambda m: None)
            summary.append("каталог off-plan PF обновлён")
        except Exception as error:  # noqa: BLE001
            summary.append(f"каталог off-plan PF не обновился: {str(error)[:120]} — беру прошлый")

    for client_dir, client in clients:
        try:
            lines = run_client(client_dir, client, today, args.dry_run)
        except Exception:  # noqa: BLE001
            lines = ["ОШИБКА: " + traceback.format_exc().strip().splitlines()[-1]]
            print(traceback.format_exc())

        for line in lines:
            print("  " + line)
        summary.append(f"\n## {client['name']}\n" + "\n".join(f"- {l}" for l in lines))

        if not args.dry_run:
            try:
                write_log_sheet(client, today, lines)
            except Exception as error:  # noqa: BLE001
                print(f"  лог в таблицу не записался: {str(error)[:100]}")
            log_md = client_dir / "log.md"
            with log_md.open("a", encoding="utf-8") as fh:
                fh.write(f"\n### {started:%d.%m.%Y %H:%M}\n" + "\n".join(f"- {l}" for l in lines) + "\n")

    text = "\n".join(summary)
    (LOG_DIR / "daily.log").open("a", encoding="utf-8").write(text + "\n\n")
    (LOG_DIR / "last_report.md").write_text(text, encoding="utf-8")
    print(f"\nГотово за {(dt.datetime.now() - started).seconds} с. Отчёт: logs/last_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
