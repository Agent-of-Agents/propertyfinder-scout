"""Презентация off-plan проекта — тот же утверждённый стиль, что у lib/pdf_brochure.py.

Единица — проект × тип квартиры (1BR, 2BR …). Источник данных — карточка
проекта на Property Finder: рендеры застройщика (чистые, без водяных знаков),
планировки типа, платёжный план, сроки, генплан, описание, удобства.

Слайды 16:9, белая вёрстка, английский, без контактов и логотипов.
Цена — «from», как её заявляет застройщик; рядом пересчёт в USD.

Структура:
  1. обложка          — рендер + название, район · застройщик, цена от
  2. проект           — факты: застройщик, локация, сдача, стройка, продажи, владение
  3. квартира         — тип, площади, цена от, планировка справа (если есть)
  4. платёжный план   — полоса долей и суммы в AED от цены «от»
  5. галерея          — все рендеры проекта
  6. генплан          — если есть
  7. заключение       — тезисы для клиента, цена в рамке, полоса кадров
"""

from __future__ import annotations

import re
from pathlib import Path

from .pdf_brochure import (ACCENT, AED_TO_USD, G, H, HAIR, INK, M, MUTED, PANEL, PAPER, W,
                           Brochure, _contain, _fit, _gallery_patterns, money)
from . import pf_projects as pf

SQFT_TO_M2 = pf.SQFT_TO_M2

PHASE_EN = {
    "down_payment": "Down payment",
    "during_construction": "During construction",
    "handover": "On handover",
    "on_handover": "On handover",
    "after_handover": "Post-handover",
    "post_handover": "Post-handover",
}
PHASE_FILL = {
    "down_payment": (163, 94, 65),
    "during_construction": (196, 150, 128),
    "handover": (60, 60, 60),
    "on_handover": (60, 60, 60),
    "after_handover": (150, 150, 150),
    "post_handover": (150, 150, 150),
}
SALES_EN = {
    "waiting_for_sales_start": "Pre-launch — sales not yet open",
    "booking_started": "Booking open",
    "sales_started": "On sale",
    "on_sale": "On sale",
}
CONSTRUCTION_EN = {"not_started": "Not started", "under_construction": "Under construction", "completed": "Completed"}


def type_en(key: str) -> str:
    return "Studio" if key == "studio" else f"{key} Bedroom"


def _quarter_en(date_iso: str) -> str:
    return pf.quarter(date_iso)


def _wrap_amenities(items: list[str], limit: int = 12) -> list[str]:
    return [a for a in items if a][:limit]


def _truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(". ", 1)[0]
    return cut + "." if cut and not cut.endswith(".") else cut


# ----------------------------------------------------------------------- сборка

def build(project: dict, type_key: str, photos: list[Path], plans: list[Path],
          master_plan: Path | None, notes: list[str], output: Path,
          pre_handover_cash: int | None = None, plan: dict | None = None) -> Path:
    pdf = Brochure()
    photos = [p for p in photos if p.exists()]
    unit = (project.get("types") or {}).get(type_key) or {}
    price = unit.get("price_from") or project.get("starting_price") or 0
    price_text = f"From {money(price)} AED" if price else "Price on request"
    usd_text = f"≈ {money(price / AED_TO_USD)} USD" if price else ""
    plan = plan or _best_plan(project)
    title = project.get("title") or ""
    subtitle = " · ".join(p for p in (project.get("community"), project.get("developer")) if p)

    # -------------------------------------------------------------- 1. обложка
    pdf.sheet()
    band_h = 132
    if photos:
        pdf.image(_fit(photos[0], W, H - band_h), 0, 0, W, H - band_h)
    band_y = H - band_h
    pdf.set_fill_color(*PAPER)
    pdf.rect(0, band_y, W, band_h, style="F")
    pdf.display(title, M, band_y + 30, size=28 if len(title) < 26 else 22, width=540)
    pdf.eyebrow(subtitle, M, band_y + 76)
    pdf.set_font("display", "", 24)
    pdf.set_text_color(*INK)
    pdf.set_xy(W - M - 340, band_y + 32)
    pdf.cell(340, 32, price_text, align="R")
    pdf.set_font("body", "", 10.5)
    pdf.set_text_color(*MUTED)
    pdf.set_xy(W - M - 340, band_y + 68)
    pdf.cell(340, 14, f"{type_en(type_key)} · {usd_text}", align="R")
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(1.4)
    pdf.line(M, band_y + 22, M + 44, band_y + 22)

    # -------------------------------------------------------------- 2. проект
    pdf.sheet()
    split = W * 0.47
    if len(photos) > 1:
        pdf.image(_fit(photos[1], W - split, H), split, 0, W - split, H)
    pdf.eyebrow("The Project", M, M + 6, color=ACCENT)
    pdf.display(title, M, M + 28, size=18 if len(title) < 30 else 14, width=split - M - 30)
    y = M + 84
    inner = split - M - 34
    facts = [
        ("Developer", project.get("developer")),
        ("Location", ", ".join(p for p in (project.get("subcommunity") if project.get("subcommunity", "").lower() != title.lower() else "",
                                            project.get("community"), project.get("city")) if p)),
        ("Handover", _quarter_en(project.get("delivery_date", ""))),
        ("Construction", CONSTRUCTION_EN.get(project.get("construction_phase", ""), "") +
         (f" · {project['construction_progress']}%" if project.get("construction_progress") else "")),
        ("Sales status", SALES_EN.get(project.get("sales_phase", ""), "")),
        ("Ownership", (project.get("ownership") or "").capitalize()),
        ("Unit types", ", ".join(type_en(k) for k in sorted((project.get("types") or {}).keys(), key=_type_order))),
    ]
    for label, value in facts:
        if value:
            y = pdf.fact_row(label, str(value)[:48], M, y, inner)

    # ------------------------------------------------------------ 3. квартира
    pdf.sheet()
    pdf.eyebrow("The Residence", M, M + 6, color=ACCENT)
    pdf.display(f"{type_en(type_key)} Apartment", M, M + 28, size=20, width=split - M - 30)
    y = M + 84
    area_from, area_to = unit.get("area_from_sqft"), unit.get("area_to_sqft")
    if area_from:
        size = f"{area_from:,.0f}"
        if area_to and area_to != area_from:
            size += f" – {area_to:,.0f}"
        size += f" sqft · {area_from * SQFT_TO_M2:,.0f}"
        if area_to and area_to != area_from:
            size += f" – {area_to * SQFT_TO_M2:,.0f}"
        size += " sqm"
        y = pdf.fact_row("Size", size, M, y, inner)
    if unit.get("layouts"):
        y = pdf.fact_row("Layouts", f"{unit['layouts']} layout type{'s' if unit['layouts'] > 1 else ''}", M, y, inner)
    if price and area_from:
        y = pdf.fact_row("Price per sqm", f"from {money(price / (area_from * SQFT_TO_M2))} AED", M, y, inner)
    if pre_handover_cash and plan:
        y = pdf.fact_row("Paid before keys", f"{money(pre_handover_cash)} AED ({_shares(plan)['pre']:g}%)", M, y, inner)
    pdf.eyebrow("Price", M, y + 10, color=ACCENT)
    pdf.set_font("display", "", 24)
    pdf.set_text_color(*INK)
    pdf.set_xy(M, y + 26)
    pdf.cell(inner, 28, price_text)
    pdf.set_font("body", "", 10.5)
    pdf.set_text_color(*MUTED)
    pdf.set_xy(M, y + 58)
    pdf.cell(inner, 14, usd_text)

    right_x, right_w = split + 10, W - split - 10 - M
    if plans:
        try:
            stream, width, height = _contain(plans[0], right_w, H - 2 * M)
            pdf.image(stream, right_x + (right_w - width) / 2, M + (H - 2 * M - height) / 2, width, height)
        except Exception:  # noqa: BLE001
            plans = []
    if not plans and len(photos) > 2:
        pdf.image(_fit(photos[2], W - split, H), split, 0, W - split, H)

    # -------------------------------------------------------- 4. платёжный план
    if plan:
        pdf.sheet()
        pdf.eyebrow("Payment Plan", M, M + 6, color=ACCENT)
        label = plan.get("title") or ""
        pdf.display(plan.get("short", "") + (f"  ·  {label}" if label and label.lower() != "standard" else ""),
                    M, M + 28, size=22, width=W - 2 * M)
        bar_y, bar_h, bar_w = M + 96, 54, W - 2 * M
        x = M
        segments = [(p["label"], p["value"]) for p in plan["phases"] if p.get("value")]
        total = sum(v for _, v in segments) or 100
        for label_key, value in segments:
            seg_w = bar_w * value / total
            pdf.set_fill_color(*PHASE_FILL.get(label_key, (120, 120, 120)))
            pdf.rect(x, bar_y, seg_w, bar_h, style="F")
            if seg_w > 40:
                pdf.set_font("bodyb", "", 13)
                pdf.set_text_color(255, 255, 255)
                pdf.set_xy(x, bar_y + 18)
                pdf.cell(seg_w, 18, f"{value:g}%", align="C")
            x += seg_w
        y = bar_y + bar_h + 34
        for label_key, value in segments:
            pdf.set_fill_color(*PHASE_FILL.get(label_key, (120, 120, 120)))
            pdf.rect(M, y + 4, 10, 10, style="F")
            pdf.set_font("body", "", 12)
            pdf.set_text_color(*INK)
            pdf.set_xy(M + 20, y)
            pdf.cell(300, 18, PHASE_EN.get(label_key, label_key.replace("_", " ").capitalize()))
            pdf.set_font("bodyb", "", 12)
            pdf.set_xy(M + 320, y)
            pdf.cell(120, 18, f"{value:g}%", align="R")
            if price:
                pdf.set_font("body", "", 12)
                pdf.set_text_color(*MUTED)
                pdf.set_xy(M + 460, y)
                pdf.cell(220, 18, f"{money(price * value / 100)} AED", align="R")
            pdf.hairline(M, y + 24, W - 2 * M)
            y += 32
        miles = [(m_label, m_value) for p in plan["phases"] for (m_label, m_value) in p.get("miles", [])
                 if m_label and m_value]
        if miles and y < H - M - 40:
            pdf.set_font("body", "", 9.5)
            pdf.set_text_color(*MUTED)
            pdf.set_xy(M, y + 6)
            pdf.multi_cell(W - 2 * M, 13, "Milestones: " + "; ".join(f"{l} — {v:g}%" for l, v in miles[:8]))
        if price:
            pdf.set_font("body", "", 9)
            pdf.set_text_color(*MUTED)
            pdf.set_xy(M, H - M - 12)
            pdf.cell(W - 2 * M, 12, f"Amounts are indicative and calculated from the starting price of {money(price)} AED.")

    # -------------------------------------------------------------- 5. галерея
    rest = photos[3:] if plans else photos[2:]
    rest = list(rest)
    for boxes in _gallery_patterns(len(rest)):
        pdf.sheet()
        for (x, y_box, w, h) in boxes:
            if not rest:
                break
            pdf.image(_fit(rest.pop(0), w, h), x, y_box, w, h)

    # ------------------------------------------------------------- 6. планировки
    for extra in plans[1:3]:
        pdf.sheet()
        pdf.eyebrow("Floor Plan", M, M, color=ACCENT)
        box_w, box_h = W - 2 * M, H - 2 * M - 34
        try:
            stream, width, height = _contain(extra, box_w, box_h)
        except Exception:  # noqa: BLE001
            continue
        pdf.image(stream, (W - width) / 2, M + 34 + (box_h - height) / 2, width, height)

    # ---------------------------------------------------------------- 7. генплан
    if master_plan and master_plan.exists():
        pdf.sheet()
        pdf.eyebrow("Master Plan", M, M, color=ACCENT)
        box_w, box_h = W - 2 * M, H - 2 * M - 34
        try:
            stream, width, height = _contain(master_plan, box_w, box_h)
            pdf.image(stream, (W - width) / 2, M + 34 + (box_h - height) / 2, width, height)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 8. заключение
    pdf.sheet()
    pdf.set_fill_color(*PANEL)
    pdf.rect(0, 0, W, H, style="F")
    pdf.eyebrow("Notes", M, M + 10, color=ACCENT)
    pdf.display(title, M, M + 32, size=22 if len(title) < 30 else 16, width=W - 2 * M)

    pdf.set_font("body", "", 12.5)
    pdf.set_text_color(*INK)
    pdf.set_xy(M, M + 80)
    text = "\n".join(f"•  {n}" for n in notes) if notes else ""
    amenities = _wrap_amenities(project.get("amenities") or [], 8)
    if amenities:
        text += ("\n\n" if text else "") + "Amenities: " + ", ".join(amenities) + "."
    pdf.multi_cell(W * 0.56 - M, 18, text)

    box_x = W * 0.60
    box_w = W - M - box_x
    pdf.set_draw_color(*HAIR)
    pdf.set_line_width(0.8)
    pdf.rect(box_x, M + 74, box_w, 150)
    pdf.eyebrow(f"{type_en(type_key)} · starting price", box_x + 24, M + 100, color=MUTED)
    pdf.set_font("display", "", 24)
    pdf.set_text_color(*INK)
    pdf.set_xy(box_x + 24, M + 122)
    pdf.cell(box_w - 48, 30, price_text)
    pdf.set_font("body", "", 11)
    pdf.set_text_color(*MUTED)
    pdf.set_xy(box_x + 24, M + 158)
    pdf.cell(box_w - 48, 15, usd_text)
    if plan:
        pdf.set_xy(box_x + 24, M + 180)
        pdf.cell(box_w - 48, 15, f"Payment plan {plan.get('short', '')} · handover {_quarter_en(project.get('delivery_date', ''))}")

    strip = [p for p in photos[1:] if p.exists()][:3]
    if len(strip) == 3:
        strip_h = 118.0
        strip_y = H - M - 26 - strip_h
        strip_w = (W - 2 * M - 2 * G) / 3
        for index, photo in enumerate(strip):
            pdf.image(_fit(photo, strip_w, strip_h), M + index * (strip_w + G), strip_y, strip_w, strip_h)

    pdf.set_font("body", "", 8)
    pdf.set_text_color(*MUTED)
    pdf.set_xy(M, H - M - 12)
    pdf.cell(W - 2 * M, 12, "Prices, payment plans and handover dates are as published by the developer at the date "
                            "of preparation and do not constitute a public offer.")

    output.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output))
    return output


# ----------------------------------------------------------------------- планы

def _shares(plan: dict) -> dict:
    from .offplan import plan_shares
    return plan_shares(plan)


def _best_plan(project: dict) -> dict | None:
    plans = project.get("payment_plan_detail") or []
    if not plans:
        return None
    return sorted(plans, key=lambda p: (_shares(p)["pre"], -_shares(p)["post"]))[0]


def _type_order(key: str) -> float:
    return -1 if key == "studio" else float(key) if key.replace(".", "").isdigit() else 99
