"""Ежедневная сверка таблицы клиента с рынком.

Правила, согласованные с Алексеем:
  - новый объект       → строка вставляется СВЕРХУ, голубая заливка, 🆕 в комментарии
  - изменилась цена    → обновить цену и производные, оранжевая заливка, 💰 было → стало
  - изменился заголовок→ пересчитать вид/этаж, пометить
  - сменился агент     → обновить брокера, языки, балл 🇷🇺; порядок строк не трогать
  - пропал из выдачи   → на второй день подряд: серая заливка, зачёркивание, ⚰️ снято
  - НИКОГДА не удалять строки и не писать в колонки Алексея

Якорь строки — listing_id в колонке A. Индексы строк не хранятся:
после каждой вставки лист перечитывается.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from pathlib import Path

from . import sheets

# Колонки, которые заполняет система. Всё остальное — не трогаем.
SYSTEM_COLUMNS = {
    "listing_id", "Приоритет", "🇷🇺", "Брокер", "Агентство", "Телефон", "Языки",
    "Тип", "Площадь м²", "Цена AED", "AED/м²", "К рынку", "Вид на воду",
    "Цена/качество", "Фото", "Ссылка", "Комментарий системы", "Запрошено", "Презентация",
}
OWNER_COLUMNS = {"✅ Одобрено", "📩 Запросить", "Статус", "Моя цена", "Мой комментарий"}

COLOR_NEW = {"red": 0.83, "green": 0.90, "blue": 1.0}        # голубой
COLOR_CHANGED = {"red": 1.0, "green": 0.91, "blue": 0.78}    # оранжевый
COLOR_REMOVED = {"red": 0.89, "green": 0.89, "blue": 0.89}   # серый
COLOR_NONE = {"red": 1.0, "green": 1.0, "blue": 1.0}

MISSING_RUNS_BEFORE_REMOVED = 2
NEW_HIGHLIGHT_DAYS = 3


# ------------------------------------------------------------------ состояние

def load_state(path) -> dict:
    """Состояние сверки: файл (локально) или объект с load()/save() (MongoDB в контейнере)."""
    if hasattr(path, "load"):
        return path.load() or {"listings": {}}
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"listings": {}}


def save_state(path, state: dict) -> None:
    if hasattr(path, "save"):
        path.save(state)
        return
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- лист

class SheetView:
    """Снимок листа: шапка, строки, индексы колонок по названию, sheetId."""

    def __init__(self, spreadsheet_id: str, title: str):
        self.spreadsheet_id = spreadsheet_id
        self.title = title
        self.reload()

    def reload(self) -> None:
        rows = sheets.read_range(self.spreadsheet_id, f"{self.title}!A1:AZ1000")
        self.header = [h.strip() for h in rows[0]] if rows else []
        self.rows = rows[1:] if rows else []
        self.col = {name: i for i, name in enumerate(self.header)}
        meta = {s["title"]: s["sheet_id"] for s in sheets.list_sheets(self.spreadsheet_id)}
        self.sheet_id = meta[self.title]

    def cell(self, row: list, name: str) -> str:
        index = self.col.get(name)
        if index is None or index >= len(row):
            return ""
        return (row[index] or "").strip() if isinstance(row[index], str) else str(row[index])

    def row_of(self, listing_id: str) -> int | None:
        """Номер строки листа (1-based) по listing_id."""
        for offset, row in enumerate(self.rows):
            if self.cell(row, "listing_id") == listing_id:
                return offset + 2
        return None

    def a1(self, row_number: int, name: str) -> str:
        return f"{self.title}!{_letter(self.col[name])}{row_number}"

    def guard(self, name: str) -> None:
        if name in OWNER_COLUMNS:
            raise RuntimeError(f"Отказ записи: «{name}» — колонка Алексея")
        if name not in self.col:
            raise RuntimeError(f"В шапке нет колонки «{name}»")


def _letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


# ------------------------------------------------------------------ расчёты

def medians(fresh: list[dict]) -> dict[str, float]:
    by_type: dict[str, list[float]] = {}
    for row in fresh:
        if row.get("price_per_m2") and row.get("bedrooms"):
            by_type.setdefault(str(row["bedrooms"]), []).append(row["price_per_m2"])
    return {k: statistics.median(v) for k, v in by_type.items() if v}


def market_delta(row: dict, med: dict[str, float]) -> float | None:
    m = med.get(str(row.get("bedrooms")))
    if m and row.get("price_per_m2"):
        return (row["price_per_m2"] / m - 1) * 100
    return None


def value_label(delta: float | None) -> str:
    if delta is None:
        return "—"
    if delta <= -10:
        return "★★★ заметно ниже рынка"
    if delta <= -3:
        return "★★ ниже рынка"
    if delta < 8:
        return "★ по рынку"
    return "выше рынка"


def floor_hint(title: str) -> str:
    low = (title or "").lower()
    if "high floor" in low:
        return "высокий этаж"
    if "low floor" in low:
        return "низкий этаж"
    if "mid floor" in low or "middle floor" in low:
        return "средний этаж"
    return ""


def base_comment(row: dict, delta: float | None) -> str:
    parts = []
    if row.get("water_view"):
        parts.append("вид на воду заявлен в объявлении")
    if delta is not None:
        if delta <= -7:
            parts.append(f"на {abs(delta):.0f}% дешевле медианы по башне")
        elif delta >= 10:
            parts.append(f"на {delta:.0f}% дороже медианы — есть о чём торговаться")
    hint = floor_hint(row.get("title", ""))
    if hint:
        parts.append(hint)
    if row.get("ru_score") == 3:
        parts.append("брокер говорит по-русски")
    if not row.get("verified"):
        parts.append("объявление без верификации PF")
    return "; ".join(parts) or "—"


def system_values(row: dict, delta: float | None, comment: str) -> dict[str, object]:
    """Значения системных колонок для одной строки."""
    return {
        "listing_id": row["id"],
        "Приоритет": 1 if row.get("ru_score") == 3 else 2,
        "🇷🇺": "🇷🇺" if row.get("ru_score") == 3 else ("~" if row.get("ru_score") == 2 else ""),
        "Брокер": row.get("agent_name") or "",
        "Агентство": row.get("agency") or "",
        "Телефон": row.get("agency_phone") or "",
        "Языки": row.get("agent_languages") or "",
        "Тип": f"{row.get('bedrooms')}BR",
        "Площадь м²": row.get("size_m2") or "",
        "Цена AED": row.get("price") or "",
        "AED/м²": row.get("price_per_m2") or "",
        "К рынку": f"{delta:+.0f}%" if delta is not None else "",
        "Вид на воду": "да" if row.get("water_view") else "",
        "Цена/качество": value_label(delta),
        "Фото": row.get("images_count") or 0,
        "Ссылка": row.get("url") or "",
        "Комментарий системы": comment,
    }


# ---------------------------------------------------------------- применение

def _fill(view: SheetView, row_number: int, color: dict, strike: bool | None = None) -> dict:
    fmt: dict = {"backgroundColor": color}
    fields = "userEnteredFormat.backgroundColor"
    if strike is not None:
        fmt["textFormat"] = {"strikethrough": strike}
        fields += ",userEnteredFormat.textFormat.strikethrough"
    return {
        "repeatCell": {
            "range": {"sheetId": view.sheet_id, "startRowIndex": row_number - 1,
                      "endRowIndex": row_number, "startColumnIndex": 0,
                      "endColumnIndex": len(view.header)},
            "cell": {"userEnteredFormat": fmt},
            "fields": fields,
        }
    }


def _insert_top(view: SheetView, count: int) -> None:
    sheets.batch_update(view.spreadsheet_id, [{
        "insertDimension": {
            "range": {"sheetId": view.sheet_id, "dimension": "ROWS",
                      "startIndex": 1, "endIndex": 1 + count},
            "inheritFromBefore": False,
        }
    }])


def _write_row(view: SheetView, row_number: int, values: dict[str, object]) -> dict[str, list]:
    updates = {}
    for name, value in values.items():
        if name not in view.col:
            continue
        view.guard(name)
        updates[view.a1(row_number, name)] = [[value]]
    return updates


def _fmt_money(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


BUDGET_SLACK = 1.05        # чуть выше потолка показываем — есть о чём торговаться
BUDGET_FLOOR_SLACK = 0.90  # и чуть ниже нижней границы — по той же причине


def fits_client(client: dict, row: dict) -> bool:
    """Подходит ли объявление под бриф: тип квартиры и бюджет с обеих сторон.

    Нижняя граница — не «чем дешевле, тем лучше», а класс объекта: клиент,
    который ищет виллу за 50–120 млн, не хочет видеть виллы за 8 млн.
    Урок Таланина 18.09.2026: без нижней границы в «бюджет» попали все виллы острова.
    Таблица строится под бриф, и сверка должна смотреть теми же глазами.
    """
    bedrooms = [str(b) for b in client.get("bedrooms", [])]
    if bedrooms and str(row.get("bedrooms")) not in bedrooms:
        return False
    budget = client.get("budget") or {}
    price = row.get("price")
    if price:
        max_price = budget.get("max")
        if max_price and price > max_price * BUDGET_SLACK:
            return False
        min_price = budget.get("min")
        if min_price and price < min_price * BUDGET_FLOOR_SLACK:
            return False
    return True


def run(client: dict, fresh: list[dict], state_path: Path, today: dt.date | None = None) -> dict:
    """Сверить лист с рынком и применить изменения. Возвращает сводку."""
    today = today or dt.date.today()
    stamp = today.strftime("%d.%m")
    state = load_state(state_path)
    seen_state = state["listings"]

    # Медиана — по всей башне (так честнее), а сверка — только по брифу
    all_fresh = fresh
    fresh = [r for r in fresh if fits_client(client, r)]
    for r in fresh:
        r["agent_name"] = (r.get("agent_name") or "").strip()

    view = SheetView(client["spreadsheet_id"], client.get("sheet", "Объекты"))
    med = medians(all_fresh)
    fresh_by_id = {r["id"]: r for r in fresh}
    sheet_ids = {view.cell(r, "listing_id") for r in view.rows if view.cell(r, "listing_id")}

    report = {"new": [], "price": [], "title": [], "agent": [], "removed": [],
              "approved_removed": [], "medians": med}

    # ------------------------------------------------ 1. новые — вставка сверху
    new_rows = [r for r in fresh if r["id"] not in sheet_ids]
    new_rows.sort(key=lambda r: (-(r.get("ru_score") or 0), market_delta(r, med) or 0))

    if new_rows:
        _insert_top(view, len(new_rows))
        view.reload()
        updates, formats = {}, []
        for offset, row in enumerate(new_rows):
            row_number = 2 + offset
            delta = market_delta(row, med)
            comment = f"🆕 {stamp}; " + base_comment(row, delta)
            updates.update(_write_row(view, row_number, system_values(row, delta, comment)))
            formats.append(_fill(view, row_number, COLOR_NEW))
            seen_state[row["id"]] = {"first_seen": today.isoformat(), "last_seen": today.isoformat(),
                                     "missing": 0, "price": row.get("price"),
                                     "title": row.get("title"), "agent": row.get("agent_name")}
            report["new"].append(row)
        sheets.update_ranges(view.spreadsheet_id, updates)
        sheets.batch_update(view.spreadsheet_id, formats)
        view.reload()

    # Чекбоксы на новые строки: колонки Алексея с BOOLEAN-валидацией
    if new_rows:
        requests = []
        for name in ("✅ Одобрено", "📩 Запросить"):
            if name in view.col:
                requests.append({"setDataValidation": {
                    "range": {"sheetId": view.sheet_id, "startRowIndex": 1,
                              "endRowIndex": 1 + len(new_rows),
                              "startColumnIndex": view.col[name], "endColumnIndex": view.col[name] + 1},
                    "rule": {"condition": {"type": "BOOLEAN"}, "strict": True}}})
        if requests:
            sheets.batch_update(view.spreadsheet_id, requests)

    # ------------------------------------------------ 2. существующие — изменения
    updates, formats = {}, []
    for row in view.rows:
        listing_id = view.cell(row, "listing_id")
        if not listing_id or listing_id in {r["id"] for r in new_rows}:
            continue
        prev = seen_state.setdefault(listing_id, {"first_seen": today.isoformat(), "missing": 0})
        row_number = view.row_of(listing_id)
        current_comment = view.cell(row, "Комментарий системы")
        approved = view.cell(row, "✅ Одобрено").upper() == "TRUE"

        fresh_row = fresh_by_id.get(listing_id)
        if fresh_row is None:
            # Считаем ДНИ отсутствия, не запуски: два ручных прогона за вечер — не «снято»
            if prev.get("missing_date") != today.isoformat():
                prev["missing"] = prev.get("missing", 0) + 1
                prev["missing_date"] = today.isoformat()
            if prev["missing"] == MISSING_RUNS_BEFORE_REMOVED:
                note = f"⚰️ снято {stamp}"
                if note not in current_comment:
                    updates[view.a1(row_number, "Комментарий системы")] = [[f"{note}; {current_comment}"]]
                formats.append(_fill(view, row_number, COLOR_REMOVED, strike=True))
                report["removed"].append({"id": listing_id, "agent": view.cell(row, "Брокер"),
                                          "price": view.cell(row, "Цена AED")})
                if approved:
                    report["approved_removed"].append(listing_id)
            continue

        prev["missing"] = 0
        prev["last_seen"] = today.isoformat()
        delta = market_delta(fresh_row, med)
        notes = []
        changed = False

        old_price = prev.get("price") or _to_int(view.cell(row, "Цена AED"))
        if old_price and fresh_row.get("price") and int(old_price) != int(fresh_row["price"]):
            notes.append(f"💰 {stamp} было {_fmt_money(old_price)} → {_fmt_money(fresh_row['price'])}")
            report["price"].append({"id": listing_id, "old": old_price, "new": fresh_row["price"],
                                    "agent": fresh_row.get("agent_name")})
            changed = True

        old_title = prev.get("title")
        if old_title and fresh_row.get("title") and old_title != fresh_row["title"]:
            notes.append(f"заголовок {stamp}: было «{old_title[:40]}»")
            report["title"].append({"id": listing_id, "old": old_title, "new": fresh_row["title"]})
            changed = True

        old_agent = (prev.get("agent") or view.cell(row, "Брокер")).strip()
        if old_agent and fresh_row.get("agent_name") and old_agent != fresh_row["agent_name"]:
            notes.append(f"агент {stamp}: был {old_agent}")
            report["agent"].append({"id": listing_id, "old": old_agent, "new": fresh_row["agent_name"]})
            changed = True

        prev.update({"price": fresh_row.get("price"), "title": fresh_row.get("title"),
                     "agent": fresh_row.get("agent_name")})

        # Комментарий: сохраняем ручные пометки (после «;» от attach_plan) — берём хвост
        keep = [p.strip() for p in current_comment.split(";") if "личный WhatsApp" in p]
        comment = "; ".join(notes + [base_comment(fresh_row, delta)] + keep)
        # 🆕 остаётся, пока строка молодая и без галочек
        first_seen = dt.date.fromisoformat(prev.get("first_seen", today.isoformat()))
        is_young = (today - first_seen).days < NEW_HIGHLIGHT_DAYS
        has_mark = approved or view.cell(row, "📩 Запросить").upper() == "TRUE"
        if current_comment.startswith("🆕") and is_young and not has_mark:
            comment = current_comment.split(";")[0] + "; " + comment
        elif current_comment.startswith("🆕"):
            formats.append(_fill(view, row_number, COLOR_NONE))

        values = system_values(fresh_row, delta, comment)
        values.pop("listing_id")
        updates.update(_write_row(view, row_number, values))
        if changed:
            formats.append(_fill(view, row_number, COLOR_CHANGED))

    if updates:
        sheets.update_ranges(view.spreadsheet_id, updates)
    if formats:
        sheets.batch_update(view.spreadsheet_id, formats)

    state["last_run"] = today.isoformat()
    save_state(state_path, state)
    return report


def _to_int(value: str) -> int | None:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else None
