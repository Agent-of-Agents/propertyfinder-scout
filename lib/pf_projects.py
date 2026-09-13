"""Off-plan проекты застройщиков с Property Finder («New projects»).

Бесплатная замена платным каталогам (Reelly, Alnair): раздел
https://www.propertyfinder.ae/en/new-projects/lp/dubai отдаёт весь JSON
прямо в странице (__NEXT_DATA__), браузер и API-ключ не нужны.

Что забираем по проекту:
  - застройщик, район, ссылка
  - фаза продаж (ждём старта / бронирование открыто / …) и дата старта продаж
  - стадия стройки и срок сдачи
  - платёжный план по фазам («20/50/30», есть ли post-handover), первый взнос
  - цена «от» по типам квартир (студия, 1BR, 2BR, …) с площадями
  - реальные сделки DLD по проекту (медиана AED/фт²)

Чего в PF нет и не будет: брошюр и наличия по конкретным юнитам.

Данные складываются в data/pf_projects/: список на дату и кэш карточек.
Карточка перечитывается, если изменилась цена/фаза/сдача или она старше
DETAIL_TTL_DAYS.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterator

BASE = "https://www.propertyfinder.ae"
LIST_URL = BASE + "/en/new-projects/lp/dubai?page={page}"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "pf_projects"
DETAIL_TTL_DAYS = 14
SCHEMA_VERSION = 2   # поднять при изменении parse_detail — кэш перечитается
PAUSE = 1.0          # пауза между запросами, секунд
RETRIES = 3

# Как PF называет фазы — и как это читать по-русски
SALES_PHASES = {
    "waiting_for_sales_start": "ждём старта продаж",
    "booking_started": "бронирование открыто",
    "sales_started": "продажи открыты",
    "on_sale": "в продаже",
    "sold_out": "распродано",
}
CONSTRUCTION_PHASES = {
    "not_started": "стройка не начата",
    "under_construction": "строится",
    "completed": "сдан",
}
PLAN_PHASES = {
    "down_payment": "взнос",
    "during_construction": "стройка",
    "on_handover": "сдача",
    "handover": "сдача",
    "post_handover": "после сдачи",
    "after_handover": "после сдачи",
}
POST_HANDOVER_LABELS = {"post_handover", "after_handover"}

SQFT_TO_M2 = 0.09290304


# ------------------------------------------------------------------ сеть

def fetch(url: str, timeout: int = 40) -> str:
    """GET с повторами: PF иногда отвечает 429/5xx, через пару секунд отдаёт."""
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            last = error
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"PF не отдал {url}: {last}")


def page_props(html: str) -> dict:
    match = NEXT_DATA.search(html)
    if not match:
        raise RuntimeError("в странице нет __NEXT_DATA__ — PF сменил вёрстку или закрыл доступ")
    return json.loads(match.group(1))["props"]["pageProps"]


# ------------------------------------------------------------------ список

def iter_list_pages(max_pages: int | None = None,
                    progress: Callable[[str], None] | None = None) -> Iterator[list[dict]]:
    """Страницы списка проектов по Дубаю, по 24 карточки."""
    page, total = 1, None
    while total is None or page <= total:
        if max_pages and page > max_pages:
            break
        props = page_props(fetch(LIST_URL.format(page=page)))
        result = props["searchResult"]
        total = result["meta"]["pagination"]["total"]
        if progress:
            progress(f"список {page}/{total}")
        yield result["data"]["projects"]
        page += 1
        time.sleep(PAUSE)


def summary(project: dict) -> dict:
    """Короткая карточка из списка — этого хватает для фильтра до загрузки деталей."""
    location = project.get("location") or {}
    developer = project.get("developer") or {}
    return {
        "project_id": project["id"],
        "title": project.get("title") or "",
        "developer": developer.get("name") or "",
        "developer_id": developer.get("id") or "",
        "location_full": location.get("fullName") or "",
        "url": BASE + project["shareUrl"] if project.get("shareUrl") else "",
        "campaign_type": project.get("campaignType") or "",
        "sales_phase": project.get("salesPhase") or "",
        "sales_start": (project.get("salesStartDate") or "")[:10],
        "construction_phase": project.get("constructionPhase") or "",
        "delivery_date": (project.get("deliveryDate") or "")[:10],
        "starting_price": project.get("startingPrice") or 0,
        "down_payment": project.get("downPaymentPercentage"),
        "payment_plans": list(project.get("paymentPlans") or []),
        "bedrooms": [str(b) for b in (project.get("bedrooms") or [])],
        "property_types": list(project.get("propertyTypes") or []),
        "stock": project.get("stockAvailability") or "",
        "hotness": project.get("hotnessLevel"),
    }


# ------------------------------------------------------------------ карточка

def fetch_detail(url: str) -> dict:
    props = page_props(fetch(url))
    return {
        "detail": props.get("detailResult") or {},
        "transactions": props.get("historicalTransactions") or {},
    }


def _plan(plan: dict) -> dict:
    phases = []
    for phase in plan.get("phases") or []:
        phases.append({
            "label": phase.get("label") or "",
            "value": phase.get("value"),
            "miles": [(m.get("label") or "", m.get("value")) for m in phase.get("miles") or []],
        })
    values = [p["value"] for p in phases if p["value"] is not None]
    return {
        "title": plan.get("title") or "",
        "phases": phases,
        "short": "/".join(str(int(v)) if float(v).is_integer() else str(v) for v in values),
        "post_handover": any(p["label"] in POST_HANDOVER_LABELS and p["value"] for p in phases),
    }


def _unit_types(detail: dict) -> dict[str, dict]:
    """Цена «от» и площади по типам: ключи 'studio', '1', '2', … ."""
    out: dict[str, dict] = {}
    for building in detail.get("units") or []:
        for group in building.get("units") or []:
            ptype = group.get("propertyType") or ""
            for item in group.get("list") or []:
                bedrooms = item.get("bedrooms")
                key = "studio" if bedrooms in (0, "0") else str(bedrooms)
                if bedrooms is None:
                    continue
                layouts = item.get("layouts") or []
                current = out.get(key)
                plan_urls = [u for l in layouts for u in (l.get("floorPlans") or [])]
                candidate = {
                    "property_type": ptype,
                    "price_from": item.get("startingPrice") or 0,
                    "area_from_sqft": item.get("areaFrom"),
                    "area_to_sqft": item.get("areaTo"),
                    "layouts": len(layouts),
                    "floor_plans": len(plan_urls),
                    "floor_plan_urls": plan_urls,
                    "layout_areas": [l.get("area") for l in layouts if l.get("area")],
                    "total_units": item.get("totalUnits") or 0,
                }
                # Один тип может встречаться в нескольких корпусах — берём минимальную цену
                if current is None or (candidate["price_from"] and
                                       (not current["price_from"] or candidate["price_from"] < current["price_from"])):
                    if current:
                        candidate["area_from_sqft"] = min(filter(None, [candidate["area_from_sqft"], current["area_from_sqft"]]), default=None)
                        candidate["area_to_sqft"] = max(filter(None, [candidate["area_to_sqft"], current["area_to_sqft"]]), default=None)
                        candidate["layouts"] += current["layouts"]
                        candidate["floor_plans"] += current["floor_plans"]
                        candidate["floor_plan_urls"] += current["floor_plan_urls"]
                        candidate["layout_areas"] += current["layout_areas"]
                    out[key] = candidate
    return out


def _dld(transactions: dict) -> dict:
    sales = transactions.get("sale") or []
    ppsf = [t["price"] / t["propertySize"] for t in sales
            if t.get("price") and t.get("propertySize")]
    dates = sorted(t.get("transactionDate") or "" for t in sales)
    return {
        "count": len(sales),
        "median_ppsf": round(statistics.median(ppsf)) if ppsf else None,
        "min_ppsf": round(min(ppsf)) if ppsf else None,
        "last_date": dates[-1] if dates else "",
        "by_bedrooms": _dld_by_bedrooms(sales),
    }


def _dld_by_bedrooms(sales: list[dict]) -> dict[str, dict]:
    groups: dict[str, list[float]] = {}
    for t in sales:
        if not (t.get("price") and t.get("propertySize")):
            continue
        key = "studio" if t.get("bedrooms") in (0, "0") else str(t.get("bedrooms"))
        groups.setdefault(key, []).append(t["price"] / t["propertySize"])
    return {k: {"count": len(v), "median_ppsf": round(statistics.median(v))} for k, v in groups.items()}


def _plain(html_text: str) -> str:
    """Описание без тегов и лишних пробелов."""
    import html as htmllib
    text = re.sub(r"<br\s*/?>|</p>", "\n", html_text)
    text = htmllib.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def parse_detail(raw: dict, base: dict | None = None) -> dict:
    """Нормализованная карточка проекта из detailResult + сделок."""
    detail, transactions = raw["detail"], raw.get("transactions") or {}
    tree = detail.get("locationTree") or []
    names = [node.get("name") or "" for node in tree]
    plans = [_plan(p) for p in detail.get("paymentPlans") or []]
    timeline = [
        {"key": p.get("phaseKey") or "", "title": (p.get("title") or "").strip(),
         "date": (p.get("rawDate") or "")[:10], "done": bool(p.get("completed")),
         "category": p.get("category") or ""}
        for p in detail.get("timelinePhases") or []
    ]
    record = dict(base or {})
    record.update({
        "project_id": detail.get("id") or record.get("project_id", ""),
        "title": detail.get("title") or record.get("title", ""),
        "developer": (detail.get("developer") or {}).get("name") or record.get("developer", ""),
        "url": BASE + "/en/new-projects/" + detail["slug"] if detail.get("slug") else record.get("url", ""),
        "city": names[0] if names else "",
        "community": names[1] if len(names) > 1 else "",
        "subcommunity": names[2] if len(names) > 2 else "",
        "location_full": (detail.get("location") or {}).get("fullName") or record.get("location_full", ""),
        "sales_phase": detail.get("salesPhase") or "",
        "sales_start": (detail.get("salesStartDate") or "")[:10],
        "construction_phase": detail.get("constructionPhase") or "",
        "construction_progress": detail.get("constructionProgress"),
        "delivery_date": (detail.get("deliveryDate") or "")[:10],
        "ownership": detail.get("ownershipType") or "",
        "property_types": list(detail.get("propertyTypes") or []),
        "starting_price": detail.get("startingPrice") or 0,
        "min_resale_price": detail.get("minResalePrice"),
        "stock": detail.get("stockAvailability") or "",
        "hotness": detail.get("hotnessLevel"),
        "payment_plans": [p["short"] for p in plans if p["short"]],
        "payment_plan_detail": plans,
        "post_handover": any(p["post_handover"] for p in plans),
        "types": _unit_types(detail),
        "timeline": timeline,
        "brochure_url": detail.get("brochureUrl") or "",
        "images": [img.get("source") for img in detail.get("images") or []
                   if img.get("type") == "image" and img.get("source")],
        "master_plan_image": ((detail.get("masterPlan") or {}).get("image") or "") if isinstance(detail.get("masterPlan"), dict) else "",
        "description": _plain(detail.get("description") or ""),
        "amenities": [a.get("name") for a in detail.get("amenities") or [] if isinstance(a, dict) and a.get("name")],
        "schema": SCHEMA_VERSION,
        "dld": _dld(transactions),
        "fetched": dt.datetime.now().isoformat(timespec="seconds"),
    })
    if record.get("down_payment") is None:
        for plan in plans:
            for phase in plan["phases"]:
                if phase["label"] == "down_payment" and phase["value"]:
                    record["down_payment"] = phase["value"]
    return record


# ------------------------------------------------------------------ кэш и сбор

def _signature(item: dict) -> str:
    return "|".join(str(item.get(k, "")) for k in
                    ("starting_price", "sales_phase", "delivery_date", "construction_phase", "stock"))


def _detail_path(project_id: str) -> Path:
    return DATA_DIR / "details" / f"{project_id}.json"


def load_cached(project_id: str) -> dict | None:
    path = _detail_path(project_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _stale(cached: dict | None, item: dict, today: dt.date) -> bool:
    if not cached:
        return True
    if cached.get("schema") != SCHEMA_VERSION:
        return True
    if cached.get("signature") != _signature(item):
        return True
    fetched = cached.get("fetched", "")[:10]
    try:
        age = (today - dt.date.fromisoformat(fetched)).days
    except ValueError:
        return True
    return age >= DETAIL_TTL_DAYS


def collect(developer_only: bool = True, with_details: bool = True,
            max_pages: int | None = None, max_details: int | None = None,
            progress: Callable[[str], None] | None = print) -> list[dict]:
    """Собрать все проекты Дубая; вернуть нормализованные карточки.

    developer_only — только предложения застройщиков (без ресейла off-plan).
    with_details   — дотянуть карточки (планы, типы, сделки); иначе только список.
    """
    today = dt.date.today()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "details").mkdir(exist_ok=True)

    items: list[dict] = []
    for page in iter_list_pages(max_pages=max_pages, progress=progress):
        for project in page:
            item = summary(project)
            if developer_only and item["campaign_type"] != "project":
                continue
            items.append(item)

    (DATA_DIR / f"list_{today:%Y-%m-%d}.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    if progress:
        progress(f"в списке {len(items)} проектов")

    if not with_details:
        return items

    records: list[dict] = []
    fetched = 0
    for index, item in enumerate(items, 1):
        cached = load_cached(item["project_id"])
        if _stale(cached, item, today) and item["url"] and (max_details is None or fetched < max_details):
            try:
                record = parse_detail(fetch_detail(item["url"]), base=item)
                record["signature"] = _signature(item)
                _detail_path(item["project_id"]).write_text(
                    json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
                cached = record
                fetched += 1
                if progress and fetched % 25 == 0:
                    progress(f"карточки: {fetched} загружено, {index}/{len(items)} пройдено")
                time.sleep(PAUSE)
            except Exception as error:  # noqa: BLE001 — одна битая карточка не должна ронять сбор
                if progress:
                    progress(f"  пропуск {item['title']}: {str(error)[:80]}")
                cached = cached or item
        records.append(cached or item)

    (DATA_DIR / "projects.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    if progress:
        progress(f"готово: {len(records)} проектов, карточек обновлено {fetched}")
    return records


def load_projects() -> list[dict]:
    path = DATA_DIR / "projects.json"
    if not path.exists():
        raise FileNotFoundError("Нет data/pf_projects/projects.json — сначала запустите сбор: "
                                "scripts\\offplan_market.py")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ подписи

def sales_phase_ru(key: str) -> str:
    return SALES_PHASES.get(key, key.replace("_", " ") if key else "")


def construction_ru(key: str, progress=None) -> str:
    text = CONSTRUCTION_PHASES.get(key, key.replace("_", " ") if key else "")
    if progress:
        text += f" {progress}%"
    return text


def plan_ru(plan: dict) -> str:
    """«20/50/30 · после сдачи 30%» — из детального плана."""
    if not plan.get("phases"):
        return plan.get("short", "")
    parts = []
    for phase in plan["phases"]:
        label = PLAN_PHASES.get(phase["label"], phase["label"])
        if phase["value"] is not None:
            parts.append(f"{label} {phase['value']:g}%")
    return ", ".join(parts)


def type_label(key: str) -> str:
    return "Студия" if key == "studio" else f"{key}BR"


def quarter(date_iso: str) -> str:
    """'2027-06-01' → 'Q2 2027'."""
    if not date_iso:
        return ""
    try:
        d = dt.date.fromisoformat(date_iso[:10])
    except ValueError:
        return date_iso
    return f"Q{(d.month - 1) // 3 + 1} {d.year}"
