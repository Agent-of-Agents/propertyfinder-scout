"""Дистресс-источники: объекты ниже рынка по Дубаю.

Три источника, список закрыт:
  - distressonly.deals            — HTML с готовыми полями OP+DLD / selling / discount
  - Google-таблица офф-плана      — CSV-экспорт, метки NEW / NEW PRICE
  - t.me/distress_deal            — публичный канал через веб-зеркало t.me/s/

Каждый прогон: собрать всё, сопоставить с брифами активных клиентов,
новые совпадения дописать в лист «Дистресс» книги клиента.
"""

from __future__ import annotations

import csv
import datetime as dt
import html as htmllib
import io
import re
import urllib.request

from . import sheets

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0"

SOURCES = {
    "site": "https://distressonly.deals/",
    "sheet": "https://docs.google.com/spreadsheets/d/1Yh8vHwC4Ec3ee2Q8JOTAH6s0gB_SqgpfxaQGVVN3GtA/export?format=csv&gid=0",
    "telegram": "https://t.me/s/distress_deal",
}

HEADERS = ["Проект", "Тип", "Сдача", "Цена AED", "OP+DLD", "Дисконт", "Источник",
           "Комментарий", "Ссылка", "Найдено"]


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", errors="replace")


def _num(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


# ------------------------------------------------------------------ источники

def from_sheet() -> list[dict]:
    items = []
    reader = csv.reader(io.StringIO(_get(SOURCES["sheet"])))
    location = ""
    for row in reader:
        if len(row) < 8 or not row[2].strip():
            continue
        location = row[1].strip() or location
        price, op = _num(row[5]), _num(row[6])
        if not price:
            continue
        discount = f"{(1 - price / op) * 100:.1f}%" if op and op > price else ""
        items.append({
            "project": row[2].strip(), "type": row[3].strip(), "handover": row[4].strip(),
            "price": price, "op": op or "", "discount": discount, "source": "Google-таблица офф-плана",
            "link": row[7].strip(), "flag": row[0].strip(), "location": location,
        })
    return items


def from_site() -> list[dict]:
    raw = _get(SOURCES["site"])
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.S)
    parts = [htmllib.unescape(p).strip() for p in re.sub(r"<[^>]+>", "|", txt).split("|")]
    parts = [p for p in parts if p]
    items = []
    for i, p in enumerate(parts):
        if not p.startswith("SELLING PRICE"):
            continue
        window = parts[max(0, i - 16):i + 4]
        price = _num(p)
        op = next((_num(w) for w in window if w.startswith("OP+DLD")), None)
        disc = next((w for w in window if w.startswith("Discount")), "")
        m = re.search(r"\(([\d.,]+%)\)", disc)
        project = next((w for w in reversed(window[:-4]) if len(w) > 3 and not any(
            k in w for k in ("Price", "AED", "sqft", "Bedroom", "Handover", "Type", "Area"))), "")
        items.append({
            "project": project, "type": "", "handover": "", "price": price, "op": op or "",
            "discount": m.group(1).replace(",", ".") if m else "", "source": "distressonly.deals",
            "link": SOURCES["site"], "flag": "", "location": "",
        })
    return items


def from_telegram() -> list[dict]:
    raw = _get(SOURCES["telegram"])
    items = []
    for m in re.finditer(r'tgme_widget_message_text[^>]*>(.*?)</div>', raw, re.S):
        text = htmllib.unescape(re.sub(r"<br\s*/?>", "\n", m.group(1)))
        text = re.sub(r"<[^>]+>", "", text).strip()
        price = None
        pm = re.search(r"(\d[\d,. ]{5,})\s*(AED|aed|дирх)", text)
        if pm:
            price = _num(pm.group(1))
        items.append({"project": text.split("\n")[0][:60], "type": "", "handover": "",
                      "price": price or "", "op": "", "discount": "", "source": "t.me/distress_deal",
                      "link": SOURCES["telegram"], "flag": "", "location": "", "text": text})
    return items


def collect_all() -> list[dict]:
    out = []
    for fn in (from_sheet, from_site, from_telegram):
        try:
            out.extend(fn())
        except Exception as error:  # noqa: BLE001
            print(f"  дистресс: {fn.__name__} не отдал — {str(error)[:80]}")
    return out


# --------------------------------------------------------------- сопоставление

def matches(client: dict, items: list[dict]) -> list[dict]:
    keys = [k.lower() for k in client.get("distress_keywords", [])]
    budget = client.get("budget") or {}
    lo, hi = budget.get("min", 0), budget.get("max", 10**12)
    found = []
    for it in items:
        hay = " ".join(str(it.get(k, "")) for k in ("project", "text", "location")).lower()
        if not any(k in hay for k in keys):
            continue
        price = it.get("price")
        in_budget = (not price) or (lo * 0.9 <= price <= hi * 1.1)
        it = dict(it)
        it["comment"] = ("В бюджет" if in_budget else "Вне бюджета, тот же проект") + (
            f", дисконт {it['discount']}" if it.get("discount") else "")
        found.append(it)
    return found


def _key(it: dict) -> str:
    return f"{it['source']}|{it['project'][:40]}|{it.get('price')}"


def write_new(client: dict, found: list[dict], today: dt.date) -> list[dict]:
    """Дописать в лист «Дистресс» то, чего там ещё нет."""
    sid = client["spreadsheet_id"]
    sheets.create_sheet(sid, "Дистресс")
    rows = sheets.read_range(sid, "Дистресс!A1:J500")
    if not rows:
        sheets.update_range(sid, "Дистресс!A1", [HEADERS])
        rows = [HEADERS]
    header = rows[0]
    existing = set()
    for r in rows[1:]:
        g = lambda name: r[header.index(name)] if name in header and header.index(name) < len(r) else ""  # noqa: E731
        existing.add(f"{g('Источник')}|{g('Проект')[:40]}|{_num(g('Цена AED'))}")

    fresh = [it for it in found if _key(it) not in existing]
    if fresh:
        sheets.append_rows(sid, "Дистресс!A:J", [[
            it["project"], it["type"], it["handover"], it.get("price") or "", it.get("op") or "",
            it.get("discount") or "", it["source"], it["comment"], it["link"], today.strftime("%d.%m.%Y"),
        ] for it in fresh])
    return fresh
