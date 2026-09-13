"""Подбор off-plan проектов под бриф клиента.

Источник — data/pf_projects/projects.json (см. lib/pf_projects.py).
Единица подбора — «проект × тип квартиры»: клиент с брифом 1BR+2BR видит
две строки по одному проекту, каждая со своей ценой «от» и площадью.

Бриф в client.json, блок "offplan":
  {
    "bedrooms": ["1", "2"],              // "studio", "1", "2", …
    "budget": {"max": 3000000},          // потолок по цене «от» типа
    "communities": ["Dubai Marina"],     // подстрока в локации, пусто = весь Дубай
    "delivery_by": "2029-12-31",         // сдача не позже
    "developers": [],                    // белый список застройщиков (подстрока)
    "exclude_developers": [],
    "post_handover": false,              // нужен план с оплатой после сдачи
    "max_down_payment": 20,              // первый взнос не выше, %
    "launches_only": false,              // только пресейл / бронирование
    "pre_handover_cash_max": 1836250,    // жёстко: цена × доля до ключей ≤ этой суммы, AED
    "prefer_pre_handover_max": 40,       // мягко: бонус, если до ключей ≤ 40%
    "prefer_handover_min": 60,           // мягко: бонус, если на ключах ≥ 60%
    "prefer_post_handover_min": 60,      // мягко: бонус, если после ключей ≥ 60%
    "sheet": "Off-plan"                  // имя листа; у клиента может быть список брифов
  }

Если у клиента несколько брифов (два района с разными условиями) — "offplan"
может быть списком, каждый со своим "sheet".

Скоринг объясняемый: каждая составляющая пишется в «Комментарий системы».
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict

from . import pf_projects as pf

SQFT_TO_M2 = pf.SQFT_TO_M2

HEADERS = [
    "listing_id", "✅ Одобрено", "Балл", "Проект", "Застройщик", "Район", "Локация",
    "Фаза продаж", "Старт продаж", "Сдача", "Стройка", "Тип", "Цена от AED",
    "Площадь м²", "AED/м²", "К району", "DLD AED/м²", "К DLD", "План оплаты",
    "Взнос %", "До ключей %", "До ключей AED", "На ключах %", "После ключей %",
    "Планировок", "Ссылка", "Брошюра", "Презентация", "Комментарий системы",
    "Статус", "Мой комментарий",
]
OWNER_COLUMNS = {"✅ Одобрено", "Статус", "Мой комментарий"}
SYSTEM_COLUMNS = set(HEADERS) - OWNER_COLUMNS

LAUNCH_PHASES = {"waiting_for_sales_start", "booking_started"}
BUDGET_SLACK = 1.05
SUSPICIOUS_PPM2 = 7000     # ниже этого AED/м² в Дубае off-plan не бывает — ошибка данных

# Застройщики, которых Алексей считает первым эшелоном (11.09.2026) — подстрока в названии
TOP_DEVELOPERS = ["emaar", "damac", "sobha", "nakheel", "meraas", "binghatti", "ellington", "danube",
                  "omniyat", "select group", "imtiaz", "arada", "nshama", "aldar", "mag ", "deyaar"]


# ------------------------------------------------------------------ вспомогательные

def client_briefs(client: dict) -> list[dict]:
    """Блок offplan клиента как список брифов (один словарь или список)."""
    raw = client.get("offplan")
    if not raw:
        return []
    return [dict(b) for b in raw] if isinstance(raw, list) else [dict(raw)]


def _m2(sqft) -> float | None:
    return round(sqft * SQFT_TO_M2, 1) if sqft else None


def _ppm2(price, sqft) -> int | None:
    return round(price / (sqft * SQFT_TO_M2)) if price and sqft else None


def _contains(haystack: str, needles: list[str]) -> bool:
    low = (haystack or "").lower()
    return any(n.lower() in low for n in needles)


def _area(project: dict) -> str:
    """Район + подрайон, но без названия проекта: «Downtown» не должен ловить «The Central Downtown» в Arjan."""
    sub = project.get("subcommunity") or ""
    if sub.lower() == (project.get("title") or "").lower():
        sub = ""
    return f"{project.get('community', '')} {sub}"


def _location_label(project: dict) -> str:
    """Подрайон без города и без повтора названия проекта: «Dubai Islands, Marina Shores» → «Marina Shores»."""
    sub = project.get("subcommunity") or ""
    if sub.lower() == (project.get("title") or "").lower():
        return ""
    return sub


def community_medians(projects: list[dict]) -> dict[tuple[str, str], float]:
    """Медиана AED/м² по (район, тип) среди всех собранных проектов — база для «К району»."""
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for project in projects:
        for key, t in (project.get("types") or {}).items():
            value = _ppm2(t.get("price_from"), t.get("area_from_sqft"))
            if value and project.get("community"):
                groups[(project["community"], key)].append(value)
    return {k: statistics.median(v) for k, v in groups.items() if len(v) >= 3}


# ------------------------------------------------------------------ платёжный план

PRE_LABELS = {"down_payment", "during_construction"}
HANDOVER_LABELS = {"handover", "on_handover"}


def plan_shares(plan: dict) -> dict:
    """Доли плана: до ключей / на ключах / после ключей, в процентах."""
    shares = {"pre": 0.0, "hand": 0.0, "post": 0.0}
    for phase in plan.get("phases") or []:
        value = phase.get("value")
        if value is None:
            continue
        if phase["label"] in PRE_LABELS:
            shares["pre"] += value
        elif phase["label"] in HANDOVER_LABELS:
            shares["hand"] += value
        elif phase["label"] in pf.POST_HANDOVER_LABELS:
            shares["post"] += value
    return shares


def best_plan(project: dict, brief: dict) -> tuple[dict | None, dict]:
    """План проекта, который лучше всего ложится на бриф: меньше до ключей, больше после."""
    plans = project.get("payment_plan_detail") or []
    if not plans:
        return None, {"pre": None, "hand": None, "post": None}
    want_post = bool(brief.get("prefer_post_handover_min") or brief.get("post_handover"))
    ranked = sorted(plans, key=lambda p: (-plan_shares(p)["post"] if want_post else 0, plan_shares(p)["pre"]))
    return ranked[0], plan_shares(ranked[0])


# ------------------------------------------------------------------ фильтр

def fits(brief: dict, project: dict, type_key: str, unit: dict, today: dt.date) -> bool:
    if brief.get("launches_only") and project.get("sales_phase") not in LAUNCH_PHASES:
        return False
    if project.get("stock") == "sold_out" or project.get("sales_phase") == "sold_out":
        return False
    # Сданное и просроченное — уже не off-plan; PF такие карточки не снимает
    if project.get("construction_phase") == "completed":
        return False
    if project.get("delivery_date") and project["delivery_date"] < today.isoformat():
        return False

    max_price = (brief.get("budget") or {}).get("max")
    price = unit.get("price_from") or 0
    if not price:
        return False                       # без цены «от» строка бесполезна
    if max_price and price > max_price * BUDGET_SLACK:
        return False
    min_price = (brief.get("budget") or {}).get("min")
    if min_price and price < min_price:
        return False

    if brief.get("communities") and not _contains(_area(project), brief["communities"]):
        return False
    if brief.get("developers") and not _contains(project.get("developer", ""), brief["developers"]):
        return False
    if brief.get("exclude_developers") and _contains(project.get("developer", ""), brief["exclude_developers"]):
        return False

    delivery_by = brief.get("delivery_by")
    if delivery_by and project.get("delivery_date") and project["delivery_date"] > delivery_by:
        return False
    if delivery_by and not project.get("delivery_date"):
        return False

    if brief.get("post_handover") and not project.get("post_handover"):
        return False
    max_dp = brief.get("max_down_payment")
    if max_dp and project.get("down_payment") and project["down_payment"] > max_dp:
        return False

    # Жёсткий кэш до ключей: цена «от» × доля плана до ключей
    cash_max = brief.get("pre_handover_cash_max")
    if cash_max:
        _, shares = best_plan(project, brief)
        pre = shares["pre"]
        if pre is None:
            return False                   # без плана оценить нельзя — не показываем
        if price * pre / 100 > cash_max * BUDGET_SLACK:
            return False
    return True


# ------------------------------------------------------------------ скоринг

def score(project: dict, type_key: str, unit: dict, medians: dict,
          today: dt.date, brief: dict | None = None) -> tuple[int, list[str], dict]:
    """Балл 0–100 и список причин. Чем выше — тем интереснее инвестору.

    Веса подобраны так, чтобы максимум (~90) был недостижим без всех плюсов сразу —
    иначе топ слипается в сплошные «100».
    """
    points, reasons = 40, []
    metrics: dict = {}
    brief = brief or {}

    ppm2 = _ppm2(unit.get("price_from"), unit.get("area_from_sqft"))
    metrics["ppm2"] = ppm2

    # 1. Цена за метр против района по тому же типу
    median = medians.get((project.get("community", ""), type_key))
    suspicious = bool(ppm2 and (ppm2 < SUSPICIOUS_PPM2 or (median and ppm2 / median < 0.55)))
    if suspicious:
        # Слишком дешёвый метр — почти всегда ошибка в площади или цене на PF, не находка
        reasons.append("⚠️ цена за м² выглядит ошибочно — проверить площадь и цену")
        points -= 10
    if ppm2 and median:
        delta = (ppm2 / median - 1) * 100
        metrics["delta_area"] = delta
        if suspicious:
            pass
        elif delta <= -15:
            points += 15; reasons.append(f"на {abs(delta):.0f}% дешевле медианы района по {pf.type_label(type_key)}")
        elif delta <= -5:
            points += 8; reasons.append(f"на {abs(delta):.0f}% ниже района")
        elif delta >= 20:
            points -= 12; reasons.append(f"на {delta:.0f}% дороже района")
        elif delta >= 8:
            points -= 5

    # 2. Против реальных сделок DLD в локации
    dld = project.get("dld") or {}
    dld_ppsf = (dld.get("by_bedrooms") or {}).get(type_key, {}).get("median_ppsf") or dld.get("median_ppsf")
    if ppm2 and dld_ppsf:
        dld_ppm2 = round(dld_ppsf / SQFT_TO_M2)
        metrics["dld_ppm2"] = dld_ppm2
        delta = (ppm2 / dld_ppm2 - 1) * 100
        metrics["delta_dld"] = delta
        if suspicious:
            pass
        elif delta <= -10:
            points += 10; reasons.append(f"на {abs(delta):.0f}% ниже сделок DLD в локации")
        elif delta >= 15:
            points -= 8; reasons.append(f"на {delta:.0f}% выше сделок DLD")

    # 3. Стадия — вход на запуске обычно дешевле
    phase = project.get("sales_phase")
    if phase == "waiting_for_sales_start":
        points += 8; reasons.append("запуск ещё не начался — вход по стартовым ценам")
    elif phase == "booking_started":
        points += 5; reasons.append("бронирование открыто")

    # 4. Платёжный план
    dp = project.get("down_payment")
    if dp is not None and dp <= 10:
        points += 3; reasons.append(f"взнос {dp:g}%")
    if project.get("post_handover"):
        points += 5; reasons.append("часть оплаты после сдачи")

    # 4б. План под пожелания клиента
    plan, shares = best_plan(project, brief)
    metrics["shares"] = shares
    metrics["plan"] = plan
    if shares["pre"] is not None:
        metrics["pre_cash"] = round((unit.get("price_from") or 0) * shares["pre"] / 100)
        pre_max, hand_min, post_min = (brief.get("prefer_pre_handover_max"), brief.get("prefer_handover_min"),
                                       brief.get("prefer_post_handover_min"))
        if pre_max and shares["pre"] <= pre_max:
            points += 6; reasons.append(f"до ключей {shares['pre']:g}%")
        elif pre_max and shares["pre"] > pre_max + 15:
            points -= 6; reasons.append(f"до ключей {shares['pre']:g}% — больше, чем хотелось")
        if hand_min and shares["hand"] >= hand_min:
            points += 6; reasons.append(f"на ключах {shares['hand']:g}%")
        if post_min:
            if shares["post"] >= post_min:
                points += 10; reasons.append(f"после ключей {shares['post']:g}%")
            elif shares["post"] > 0:
                points += 4; reasons.append(f"после ключей только {shares['post']:g}%")

    # 5. Застройщик первого эшелона
    if _contains(project.get("developer", ""), TOP_DEVELOPERS):
        points += 6; reasons.append("застройщик из первого эшелона")

    # 6. Срок сдачи
    if project.get("delivery_date"):
        try:
            years = (dt.date.fromisoformat(project["delivery_date"]) - today).days / 365
            metrics["years_to_delivery"] = years
            if years <= 1.5:
                points += 3; reasons.append("сдача близко")
            elif years >= 4:
                points -= 4; reasons.append(f"сдача через {years:.0f} лет")
        except ValueError:
            pass

    # 7. Интерес рынка по данным PF
    if (project.get("hotness") or 0) >= 90:
        points += 2

    return max(0, min(100, points)), reasons, metrics


# ------------------------------------------------------------------ строки

def _listing_id(project: dict, type_key: str) -> str:
    return f"{project['project_id']}:{type_key}"


def build_rows(brief: dict, projects: list[dict], today: dt.date | None = None) -> list[dict]:
    """Строки для листа: словари по названиям колонок, отсортированы по баллу."""
    today = today or dt.date.today()
    medians = community_medians(projects)
    wanted = [str(b) for b in brief.get("bedrooms", [])] or None

    rows = []
    for project in projects:
        for type_key, unit in (project.get("types") or {}).items():
            if wanted and type_key not in wanted:
                continue
            if not fits(brief, project, type_key, unit, today):
                continue
            points, reasons, metrics = score(project, type_key, unit, medians, today, brief)
            plan = metrics.get("plan")
            plans = project.get("payment_plan_detail") or []
            plan_text = (pf.plan_ru(plan) + (f" ({plan['title']})" if len(plans) > 1 and plan.get("title") else "")
                         if plan else ", ".join(project.get("payment_plans") or []))
            shares = metrics.get("shares") or {}
            rows.append({
                "listing_id": _listing_id(project, type_key),
                "✅ Одобрено": False,
                "Балл": points,
                "Проект": project.get("title", ""),
                "Застройщик": project.get("developer", ""),
                "Район": project.get("community", ""),
                "Локация": _location_label(project),
                "Фаза продаж": pf.sales_phase_ru(project.get("sales_phase", "")),
                "Старт продаж": project.get("sales_start", ""),
                "Сдача": pf.quarter(project.get("delivery_date", "")),
                "Стройка": pf.construction_ru(project.get("construction_phase", ""), project.get("construction_progress")),
                "Тип": pf.type_label(type_key),
                "Цена от AED": unit.get("price_from") or "",
                "Площадь м²": _m2(unit.get("area_from_sqft")) or "",
                "AED/м²": metrics.get("ppm2") or "",
                "К району": round(metrics["delta_area"] / 100, 2) if "delta_area" in metrics else "",
                "DLD AED/м²": metrics.get("dld_ppm2") or "",
                "К DLD": round(metrics["delta_dld"] / 100, 2) if "delta_dld" in metrics else "",
                "План оплаты": plan_text,
                "Взнос %": project.get("down_payment") if project.get("down_payment") is not None else "",
                "До ключей %": f"{shares['pre']:g}" if shares.get("pre") is not None else "",
                "До ключей AED": metrics.get("pre_cash") or "",
                "На ключах %": f"{shares['hand']:g}" if shares.get("hand") is not None else "",
                "После ключей %": f"{shares['post']:g}" if shares.get("post") is not None else "",
                "Планировок": unit.get("layouts") or "",
                "Ссылка": project.get("url", ""),
                "Брошюра": project.get("brochure_url", ""),
                "Презентация": "",           # заполняет сборщик презентаций, при сверке не трогаем
                "Комментарий системы": "; ".join(reasons) or "—",
                "Статус": "",
                "Мой комментарий": "",
            })
    rows.sort(key=lambda r: (-r["Балл"], r["Цена от AED"] or 0))
    return rows


def as_table(rows: list[dict]) -> list[list]:
    return [HEADERS] + [[r.get(h, "") for h in HEADERS] for r in rows]
