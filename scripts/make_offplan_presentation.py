"""Презентации по одобренным off-plan строкам клиента.

    .venv\\Scripts\\python.exe scripts\\make_offplan_presentation.py --client ivanov
    .venv\\Scripts\\python.exe scripts\\make_offplan_presentation.py --client ivanov --force   (пересобрать все)
    .venv\\Scripts\\python.exe scripts\\make_offplan_presentation.py --client ivanov --local   (только PDF, без Диска)

По каждой строке с ✅ и пустой «Презентация»:
  1. карточка проекта из кэша PF (при старой схеме — перечитывается живьём)
  2. рендеры застройщика, планировки типа и генплан → clients/<папка>/offplan/<проект>/
  3. PDF в утверждённом стиле → clients/<папка>/presentations/
  4. загрузка на Общий диск: «<Клиент> — Off-plan» / <лист> / «<Проект> · <тип> · от <цена>» /
  5. ссылка в колонку «Презентация» той же строки

Цена в презентации — «from», как заявляет застройщик. Своей цены у off-plan нет.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lib import drive, offplan, offplan_sheet, pdf_offplan, pf_projects as pf, sheets  # noqa: E402
from lib.google_auth import working_folder_id  # noqa: E402
from scripts import make_presentation as mp  # noqa: E402  — upload_to_drive

CLIENTS_DIR = ROOT / "clients"
UA = pf.UA


def money(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def find_client(name: str) -> tuple[Path, dict]:
    for cfg in sorted(CLIENTS_DIR.glob("*/client.json")):
        if name.lower() in cfg.parent.name.lower():
            return cfg.parent, json.loads(cfg.read_text(encoding="utf-8"))
    raise SystemExit(f"Папка клиента с «{name}» не найдена в clients/")


# ------------------------------------------------------------------ данные проекта

def project_record(project_id: str) -> dict:
    """Карточка из кэша; если собрана старой схемой (без рендеров) — перечитать живьём."""
    cached = pf.load_cached(project_id)
    if cached and cached.get("schema") == pf.SCHEMA_VERSION:
        return cached
    if not cached or not cached.get("url"):
        raise RuntimeError(f"проект {project_id} не найден в кэше — запустите сбор каталога")
    record = pf.parse_detail(pf.fetch_detail(cached["url"]), base=cached)
    record["signature"] = cached.get("signature", "")
    pf._detail_path(project_id).write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def download(url: str, target: Path) -> Path | None:
    if target.exists():
        return target
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=40) as response:
            data = response.read()
    except Exception as error:  # noqa: BLE001
        print(f"         не скачалось {url[:60]}…: {str(error)[:60]}")
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def media(project: dict, type_key: str, folder: Path) -> tuple[list[Path], list[Path], Path | None]:
    """Рендеры проекта, планировки типа, генплан — скачанные в папку клиента."""
    photos = []
    for index, url in enumerate(project.get("images") or [], 1):
        path = download(url, folder / f"render_{index:02d}.webp")
        if path:
            photos.append(path)
    plans = []
    unit = (project.get("types") or {}).get(type_key) or {}
    for index, url in enumerate(unit.get("floor_plan_urls") or [], 1):
        path = download(url, folder / f"plan_{type_key}_{index:02d}.webp")
        if path:
            plans.append(path)
    master = download(project["master_plan_image"], folder / "master_plan.webp") if project.get("master_plan_image") else None
    return photos, plans, master


# ------------------------------------------------------------------ тезисы

def notes_en(project: dict, type_key: str, brief: dict, projects: list[dict]) -> tuple[list[str], dict]:
    """Английские тезисы для клиента из тех же метрик, что и балл в таблице."""
    unit = (project.get("types") or {}).get(type_key) or {}
    medians = offplan.community_medians(projects)
    _, reasons, metrics = offplan.score(project, type_key, unit, medians, dt.date.today(), brief)
    notes = []
    delta = metrics.get("delta_area")
    if delta is not None and delta <= -5:
        notes.append(f"Starting price per sqm is {abs(delta):.0f}% below the {project.get('community')} median for "
                     f"{pdf_offplan.type_en(type_key).lower()} units in developer launches.")
    delta_dld = metrics.get("delta_dld")
    if delta_dld is not None and delta_dld <= -8:
        notes.append(f"About {abs(delta_dld):.0f}% below recent DLD-registered transactions in the same location.")
    phase = project.get("sales_phase")
    if phase == "waiting_for_sales_start":
        notes.append("Pre-launch: sales have not opened yet, entry at launch prices.")
    elif phase == "booking_started":
        notes.append("Booking is open; units are allocated on a first-come basis.")
    shares = metrics.get("shares") or {}
    if shares.get("pre") is not None:
        line = f"Payment plan: {shares['pre']:g}% before handover"
        if shares.get("hand"):
            line += f", {shares['hand']:g}% on handover"
        if shares.get("post"):
            line += f", {shares['post']:g}% after handover"
        notes.append(line + ".")
    if project.get("delivery_date"):
        notes.append(f"Handover expected {pf.quarter(project['delivery_date'])}.")
    if (project.get("dld") or {}).get("count", 0) >= 5:
        notes.append(f"{project['dld']['count']} DLD-registered sales in this location back the price level.")
    return notes[:6], metrics


# ------------------------------------------------------------------ Диск

def unit_folder(client: dict, sheet_title: str, project: dict, type_key: str, price: int) -> dict:
    root = drive.ensure_folder(f"{client['name']} — Off-plan", working_folder_id())
    sheet_folder = drive.ensure_folder(sheet_title, root["id"])
    name = f"{project.get('title', '')[:40]} · {pf.type_label(type_key)} · от {money(price)}"
    return drive.ensure_folder(name, sheet_folder["id"])


# ------------------------------------------------------------------ обход

def process(client_dir: Path, client: dict, force: bool, local: bool) -> list[dict]:
    done = []
    projects = pf.load_projects()
    spreadsheet_id = client["spreadsheet_id"]
    for brief in offplan.client_briefs(client):
        title = brief.get("sheet", offplan_sheet.SHEET_TITLE)
        view = offplan_sheet.View(spreadsheet_id, title)
        for index, row in enumerate(view.rows):
            if view.cell(row, "✅ Одобрено").upper() != "TRUE":
                continue
            if view.cell(row, "Презентация") and not force:
                continue
            listing_id = view.cell(row, "listing_id")
            project_id, _, type_key = listing_id.partition(":")
            number = index + 2
            label = f"{view.cell(row, 'Проект')} · {view.cell(row, 'Тип')}"
            print(f"\n{title} · {label}")
            try:
                project = project_record(project_id)
                unit = (project.get("types") or {}).get(type_key) or {}
                price = unit.get("price_from") or project.get("starting_price") or 0
                folder = client_dir / "offplan" / slugify(project["title"])
                photos, plans, master = media(project, type_key, folder)
                print(f"         рендеров {len(photos)}, планировок {len(plans)}, генплан {'да' if master else 'нет'}")
                plan, shares = offplan.best_plan(project, brief)
                pre_cash = round(price * shares["pre"] / 100) if shares.get("pre") is not None and price else None
                notes, _ = notes_en(project, type_key, brief, projects)
                pdf_path = client_dir / "presentations" / f"{slugify(project['title'])}_{type_key}br.pdf".replace("studiobr", "studio")
                pdf_offplan.build(project, type_key, photos, plans, master, notes, pdf_path,
                                  pre_handover_cash=pre_cash, plan=plan)
                print(f"         PDF: {pdf_path.relative_to(ROOT)}")
                link = None
                if not local:
                    folder_meta = unit_folder(client, title, project, type_key, price)
                    link = mp.upload_to_drive(pdf_path, folder_meta)
                    if link:
                        view.guard("Презентация")
                        sheets.update_ranges(spreadsheet_id, {view.a1(number, "Презентация"): [[link]]})
                        print(f"         Диск: {link}")
                done.append({"sheet": title, "id": listing_id, "title": project["title"], "type": pf.type_label(type_key),
                             "pdf": str(pdf_path), "link": link, "status": "готово"})
            except Exception as error:  # noqa: BLE001
                print(f"         ошибка: {str(error)[:160]}")
                done.append({"sheet": title, "id": listing_id, "title": label, "status": f"ошибка: {str(error)[:100]}"})
    return done


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True)
    parser.add_argument("--force", action="store_true", help="пересобрать и те, где ссылка уже есть")
    parser.add_argument("--local", action="store_true", help="только PDF, без загрузки на Диск")
    args = parser.parse_args()
    client_dir, client = find_client(args.client)
    done = process(client_dir, client, args.force, args.local)
    print(f"\nГотово: {sum(1 for d in done if d['status'] == 'готово')} из {len(done)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
