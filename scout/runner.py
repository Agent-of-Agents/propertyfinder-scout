"""Прогон подборов: сбор → сверка → дистресс → презентации по галочкам.

Тот же цикл, что в scripts/daily.py, но поверх модели «клиент → подбор»
и хранилища scout.store (состояние сверки — в MongoDB, не в файлах).
Возвращает отчёты, из которых bot.py собирает утренние сообщения.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import statistics
import tempfile
from pathlib import Path

from lib import distress, offplan, offplan_sheet, pf_projects, propertyfinder, sync

from . import books, enrich
from .models import MARKET_OFFPLAN, MARKET_SECONDARY, SEARCH_ACTIVE, Client, Search
from .store import META, RAW, STATES, Store

log = logging.getLogger("scout.runner")

PF_MAX_PAGES = 8


# ------------------------------------------------------------------ сбор

def collect_listings(search: Search) -> list[dict]:
    """Объявления PF по всем источникам подбора, без дублей."""
    rows: list[dict] = []
    seen: set[str] = set()
    for source in search.sources:
        if source.get("kind", "propertyfinder") != "propertyfinder":
            continue
        for row in propertyfinder.collect(source["url"], max_pages=PF_MAX_PAGES):
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)
    return rows


def medians(rows: list[dict]) -> dict[str, float]:
    by_type: dict[str, list[float]] = {}
    for r in rows:
        if r.get("price_per_m2") and r.get("bedrooms") is not None:
            by_type.setdefault(str(r["bedrooms"]), []).append(r["price_per_m2"])
    return {k: statistics.median(v) for k, v in by_type.items() if v}


def market_delta(row: dict, med: dict[str, float]) -> float | None:
    m = med.get(str(row.get("bedrooms")))
    if not m or not row.get("price_per_m2"):
        return None
    return (row["price_per_m2"] / m - 1) * 100


def first_collection_report(search: Search, rows: list[dict], distress_found: int) -> dict:
    """Сводка первого сбора для карточки в тему клиента."""
    med = medians(rows)
    fits = [r for r in rows if sync.fits_client(search.as_sync_client(Client("", "")), r)]
    deltas = [d for d in (market_delta(r, med) for r in fits) if d is not None]
    return {
        "total": len(rows),
        "ru": sum(1 for r in rows if r.get("ru_score") == 3),
        "in_budget": len(fits),
        "medians": {k: v for k, v in med.items() if k in set(search.bedrooms) or not search.bedrooms},
        "best_delta": min(deltas) if deltas else None,
        "distress": distress_found,
    }


# ------------------------------------------------------------------ прогон подбора

def run_secondary(store: Store, client: Client, search: Search, today: dt.date) -> dict:
    fresh = collect_listings(search)
    if not fresh:
        return {"error": "источник не отдал ни одного объявления — сверку пропускаю", "total": 0}
    store.put(RAW, search.key, {"rows": fresh, "collected": today.isoformat()})
    report = sync.run(search.as_sync_client(client), fresh, store.state_handle(search.key), today)
    report["total"] = len(fresh)
    try:
        report["enrich"] = enrich.apply(client, search, fresh)      # серия, дубли ≈, колонки DLD
    except Exception as error:  # noqa: BLE001 — обогащение не должно валить сверку
        log.warning("Обогащение %s: %s", search.key, error)
    return report


def run_offplan(store: Store, client: Client, search: Search, today: dt.date) -> dict:
    restore_catalog(store)                       # в контейнере файла каталога нет — он в MongoDB
    try:
        projects = pf_projects.load_projects()
    except FileNotFoundError:
        return {"error": "каталог проектов ещё не собран — offplan_catalog_refresh()", "total": 0}
    rows = offplan.build_rows(search.as_offplan_brief(), projects, today)
    store.put(RAW, search.key, {"rows": [offplan_card_row(r, projects) for r in rows],
                                "collected": today.isoformat()})
    report = offplan_sheet.sync(client.spreadsheet_id, rows, search.title, today)
    report["total"] = len(rows)
    return report


# Лист говорит по-русски, карточка и кнопки — на общих полях: здесь перевод одного в другое.
OFFPLAN_FIELDS = {
    "id": "listing_id", "score": "Балл", "project": "Проект", "developer": "Застройщик",
    "community": "Район", "location": "Локация", "phase_label": "Фаза продаж",
    "sales_start": "Старт продаж", "handover": "Сдача", "progress": "Стройка",
    "type": "Тип", "price": "Цена от AED", "size_m2": "Площадь м²", "price_per_m2": "AED/м²",
    "payment_plan": "План оплаты", "plans_count": "Планировок", "url": "Ссылка",
    "brochure_url": "Брошюра", "comment": "Комментарий системы",
}


def offplan_card_row(row: dict, projects: list[dict] | None = None) -> dict:
    """Строка листа off-plan → словарь для карточки проекта в Telegram."""
    out = {key: row.get(column) for key, column in OFFPLAN_FIELDS.items()}
    out["bedrooms"] = str(row.get("Тип") or "").replace("BR", "")
    vs_area = row.get("К району")
    if vs_area not in (None, ""):
        out["vs_area"] = f"{vs_area * 100:+.0f} %"
    project_id = str(out.get("id") or "").partition(":")[0]
    for project in projects or []:
        if str(project.get("project_id")) == project_id:
            images = project.get("images") or []
            if images:
                out["photo_url"] = images[0]
            break
    return out


def run_distress(client: Client, searches: list[Search], today: dt.date) -> list[dict]:
    """Три источника против ключевых слов всех активных подборов клиента."""
    items = distress.collect_all()
    found: list[dict] = []
    for search in searches:
        if not search.distress_keywords:
            continue
        for it in distress.matches(search.as_sync_client(client), items):
            it = dict(it)
            it["search"] = search.title
            found.append(it)
    if not found:
        return []
    return distress.write_new({"spreadsheet_id": client.spreadsheet_id}, found, today)


def run_search(store: Store, client: Client, search: Search, today: dt.date | None = None) -> dict:
    """Один подбор: сбор и сверка под его рынок. Отчёт сохраняется в состоянии."""
    today = today or dt.date.today()
    try:
        if search.market == MARKET_OFFPLAN:
            report = run_offplan(store, client, search, today)
        else:
            report = run_secondary(store, client, search, today)
    except Exception as error:  # noqa: BLE001 — один сломанный источник не валит прогон
        log.exception("Прогон %s", search.key)
        report = {"error": f"{type(error).__name__}: {str(error)[:160]}", "total": 0}
    summary = _compact(report)
    summary["run_at"] = today.isoformat()
    store.update(STATES, search.key, {"last_report": summary})
    return report


def _compact(report: dict) -> dict:
    """Отчёт без тяжёлых строк — для хранения и дайджеста."""
    keep = {}
    for key in ("new", "price", "title", "agent", "removed", "approved_removed", "changed", "kept"):
        value = report.get(key)
        if isinstance(value, list):
            keep[key] = [_slim(v) for v in value[:30]]
    keep["medians"] = report.get("medians") or {}
    keep["total"] = report.get("total", 0)
    if report.get("error"):
        keep["error"] = report["error"]
    return keep


def _slim(item):
    if not isinstance(item, dict):
        return item
    fields = ("id", "price", "old", "new", "agent", "agent_name", "bedrooms", "size_m2",
              "ru_score", "title", "type", "notes", "url")
    return {k: item[k] for k in fields if k in item}


# ------------------------------------------------------------------ дневной прогон

def catalog_refresh_needed(searches: list[Search]) -> bool:
    return any(s.market == MARKET_OFFPLAN and s.status == SEARCH_ACTIVE for s in searches)


CATALOG_META_KEY = "pf_projects"


def restore_catalog(store: Store) -> bool:
    """Вернуть projects.json из MongoDB после пересборки контейнера (файлов у него нет)."""
    path = pf_projects.DATA_DIR / "projects.json"
    if path.exists():
        return True
    doc = store.get(META, CATALOG_META_KEY)
    if not doc:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc["rows"], ensure_ascii=False), encoding="utf-8")
    return True


def refresh_catalog(store: Store) -> str:
    """Каталог проектов PF — один раз за прогон, если есть активный offplan.

    Сбор ~45 минут с нуля и 5–10 минут по кэшу карточек. Итог дублируется в MongoDB,
    чтобы следующий деплой не начинал с нуля; книга-каталог на Диске обновляется следом.
    """
    projects = pf_projects.collect(progress=log.info)
    store.put(META, CATALOG_META_KEY, {"rows": projects, "collected": dt.date.today().isoformat()})
    try:
        from lib.offplan import LAUNCH_PHASES
        from scripts import offplan_market

        launches = [p for p in projects if p.get("sales_phase") in LAUNCH_PHASES]
        launches.sort(key=lambda p: (p.get("sales_start") or "", p.get("hotness") or 0), reverse=True)
        sid = offplan_market.ensure_catalog()
        offplan_market.write_sheet(sid, "Запуски", launches)
        offplan_market.write_sheet(sid, "Все проекты",
                                   sorted(projects, key=lambda p: (p.get("developer", ""), p.get("title", ""))))
    except Exception as error:  # noqa: BLE001 — книга-каталог вторична, подбор от неё не зависит
        log.warning("Книга-каталог не обновлена: %s", error)
    return f"каталог: {len(projects)} проектов"


def daily_run(store: Store, clients: list[tuple[Client, list[Search]]],
              today: dt.date | None = None) -> dict:
    """Все активные подборы всех активных клиентов. Возвращает
    {client_slug: {"client": Client, "searches": {slug: report}, "distress": [...], "errors": [...]}}.
    """
    today = today or dt.date.today()
    result: dict[str, dict] = {}
    all_searches = [s for _, ss in clients for s in ss]
    if catalog_refresh_needed(all_searches):
        try:
            refresh_catalog(store)
        except Exception as error:  # noqa: BLE001
            log.exception("Каталог проектов")
            result["_catalog_error"] = {"error": str(error)[:160]}

    for client, searches in clients:
        active = [s for s in searches if s.status == SEARCH_ACTIVE]
        entry: dict = {"client": client, "searches": {}, "distress": [], "errors": [], "log": []}
        for search in active:
            report = run_search(store, client, search, today)
            entry["searches"][search.slug] = report
            entry["log"].append((search.title, _log_line(report)))
        try:
            entry["distress"] = run_distress(client, active, today)
            if entry["distress"]:
                entry["log"].append(("Дистресс", f"{len(entry['distress'])} новых совпадений"))
        except Exception as error:  # noqa: BLE001
            log.exception("Дистресс %s", client.slug)
            entry["errors"].append(f"дистресс: {str(error)[:120]}")
        try:
            stats = {}
            for search in searches:
                if search.status == SEARCH_ACTIVE:
                    st = books.search_stats(client, search)
                    st["last_run"] = today.strftime("%d.%m.%Y")
                    stats[search.slug] = st
            books.update_summary(client, searches, stats)
            books.append_log(client, entry["log"], today)
            entry["stats"] = stats
        except Exception as error:  # noqa: BLE001
            log.exception("Сводка %s", client.slug)
            entry["errors"].append(f"сводка: {str(error)[:120]}")
        result[client.slug] = entry
    return result


def _log_line(report: dict) -> str:
    if report.get("error"):
        return f"ошибка: {report['error']}"
    parts = [f"объектов {report.get('total', 0)}"]
    for key, label in (("new", "новых"), ("price", "цен"), ("removed", "снято"), ("changed", "изменений")):
        if report.get(key):
            parts.append(f"{label} {len(report[key])}")
    return ", ".join(parts)


# ------------------------------------------------------------------ вспомогательное для PDF

def materialize_raw(store: Store, search: Search, target_dir: Path) -> Path:
    """Выгрузка объявлений подбора — во временный файл, который читает make_presentation."""
    doc = store.get(RAW, search.key) or {"rows": []}
    raw_dir = target_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / "pf_listings.json"
    path.write_text(json.dumps(doc["rows"], ensure_ascii=False), encoding="utf-8")
    return path


def workdir(client: Client) -> Path:
    """Рабочая папка клиента в контейнере: временная, всё ценное уезжает на Диск."""
    base = Path(tempfile.gettempdir()) / "scout" / client.slug
    base.mkdir(parents=True, exist_ok=True)
    return base


__all__ = ["collect_listings", "medians", "market_delta", "first_collection_report", "offplan_card_row",
           "run_search", "run_distress", "daily_run", "materialize_raw", "workdir",
           "MARKET_SECONDARY", "MARKET_OFFPLAN"]
