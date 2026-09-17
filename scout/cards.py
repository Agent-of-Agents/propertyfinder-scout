"""Тексты и кнопки для Telegram (docs/HANDOFF.md §7.3–7.6, docs/scout-dubai-telegram.html).

Модуль ничего не отправляет и не знает про aiogram: он возвращает Outgoing —
текст в HTML Telegram, клавиатуру и адрес темы. Отправляет bot.py.
Каждое сообщение об объекте начинается строкой-тегом «Клиент · Подбор».
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field

from .models import Client, Search, tag

# Значки — единый словарь, ничего сверх него (HANDOFF §7.6)
ICON_NEW, ICON_PRICE, ICON_REMOVED, ICON_DISTRESS = "🆕", "💰", "⚰️", "🔥"
ICON_RU, ICON_OK, ICON_REQ, ICON_SENT, ICON_PDF = "🇷🇺", "✅", "📩", "✔", "📄"
ICON_WARN, ICON_DATE = "⚠️", "📅"

# Коды действий в callback_data: `a|<код>|<client>|<search>|<listing>`
ACT_APPROVE, ACT_REQUEST, ACT_SKIP, ACT_SENT, ACT_CANCEL = "ap", "rq", "sk", "st", "cx"
ACT_LAUNCH, ACT_EDIT = "go", "ed"
ACT_REPLACE, ACT_ADD = "rp", "ad"
ACT_SHOW_NEW, ACT_SHOW_PRICES, ACT_SHOW_DISTRESS = "nw", "pr", "ds"
ACT_CONFIRM, ACT_CLOSE_OBJECT = "ok", "co"
ACT_PAUSE, ACT_RESUME, ACT_CLIENTS = "ps", "rs", "cl"
ACT_REBUILD = "rb"


@dataclass
class Button:
    label: str
    data: str = ""      # callback_data — действие внутри бота
    url: str = ""       # или ссылка: таблица, папка, WhatsApp


@dataclass
class Outgoing:
    text: str
    buttons: list[list[Button]] = field(default_factory=list)
    photo_url: str = ""
    file_path: str = ""
    thread_id: int | None = None       # тема Telegram; None — туда, откуда спросили
    meta: dict = field(default_factory=dict)   # адрес объекта — bot.py положит в tg_map


def esc(text: object) -> str:
    return html.escape(str(text if text is not None else ""), quote=False)


def money(value) -> str:
    try:
        return f"{int(round(float(value))):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value or "—")


def cb(action: str, client: str = "", search: str = "", listing: str = "") -> str:
    """callback_data. Больше 64 байт bot.py заменит ключом из tg_map."""
    return "|".join(["a", action, client, search, listing]).rstrip("|")


def parse_cb(data: str) -> dict | None:
    parts = (data or "").split("|")
    if len(parts) < 2 or parts[0] != "a":
        return None
    parts += [""] * (5 - len(parts))
    return {"action": parts[1], "client": parts[2], "search": parts[3], "listing": parts[4]}


def tagline(client: Client, search: Search | None = None, suffix: str = "") -> str:
    line = tag(client, search) + (f" · {suffix}" if suffix else "")
    return f"<code>{esc(line)}</code>\n"


# ------------------------------------------------------------------ карточки объектов

def listing_card(client: Client, search: Search, row: dict, position: str = "") -> Outgoing:
    """Карточка объявления secondary: фото, факты, ✅ 📩 ⏭."""
    lid = row.get("id", "")
    price = row.get("price")
    ppm = row.get("price_per_m2")
    delta = row.get("market_delta")
    ru = row.get("ru_score", 0)
    lines = [tagline(client, search, position)]
    lines.append(f"<b>{esc(row.get('bedrooms', '?'))}BR · {esc(row.get('size_m2') or '?')} м² · "
                 f"{money(price)} AED</b>")
    facts = []
    if ppm:
        facts.append(f"{money(ppm)} AED/м²")
    if delta is not None:
        facts.append(f"<b>{delta:+.0f} % к медиане</b>")
    if facts:
        lines.append(" · ".join(facts))
    view = []
    if row.get("water_view"):
        view.append("вид на воду")
    if row.get("floor_hint"):
        view.append(row["floor_hint"])
    if view:
        lines.append(esc(" · ".join(view)))
    agent = " · ".join(x for x in (row.get("agent_name"), row.get("agency")) if x)
    ru_mark = f"{ICON_RU} " if ru == 3 else ("~ " if ru == 2 else "")
    if agent:
        extra = " · Russian в профиле" if ru == 3 else ""
        lines.append(f"{ru_mark}{esc(agent)}{extra}")
    if row.get("series"):
        lines.append(f"Серия: {esc(row['series'])}")
    if row.get("comment"):
        lines.append(f"<i>{esc(row['comment'])}</i>")
    if row.get("url"):
        lines.append(f'<a href="{esc(row["url"])}">{esc(row["url"].split("//")[-1][:60])}</a>')
    return Outgoing(
        text="\n".join(lines),
        buttons=[[
            Button(f"{ICON_OK} Одобрить", cb(ACT_APPROVE, client.slug, search.slug, lid)),
            Button(f"{ICON_REQ} Запросить", cb(ACT_REQUEST, client.slug, search.slug, lid)),
            Button("⏭", cb(ACT_SKIP, client.slug, search.slug, lid)),
        ]],
        photo_url=row.get("photo_url", ""),
        meta={"client": client.slug, "search": search.slug, "listing": lid},
    )


def project_card(client: Client, search: Search, row: dict, position: str = "") -> Outgoing:
    """Карточка проекта offplan: рендер, платёжный план, балл с разбором."""
    lid = row.get("id", "")
    lines = [tagline(client, search, position)]
    lines.append(f"🏗 <b>{esc(row.get('project'))} · {esc(row.get('type'))} от {money(row.get('price'))} AED</b>")
    lines.append(f"Балл <b>{esc(row.get('score', '—'))}</b> / 100 · {esc(row.get('community'))} · {esc(row.get('developer'))}")
    phase = row.get("phase_label") or row.get("phase") or ""
    if phase:
        lines.append(f"Фаза: <b>{esc(phase)}</b>" + (f" · старт {esc(row['sales_start'])}" if row.get("sales_start") else ""))
    if row.get("handover"):
        lines.append(f"Сдача {esc(row['handover'])}" + (f" · стройка {esc(row['progress'])}" if row.get("progress") else ""))
    if row.get("size_m2") and row.get("price_per_m2"):
        lines.append(f"{esc(row['size_m2'])} м² от · {money(row['price_per_m2'])} AED/м²"
                     + (f" · <b>{esc(row['vs_area'])} к району</b>" if row.get("vs_area") else ""))
    if row.get("payment_plan"):
        lines.append(f"<pre>{esc(row['payment_plan'])}</pre>")
    if row.get("comment"):
        lines.append(f"<i>{esc(row['comment'])}</i>")
    if row.get("url"):
        lines.append(f'<a href="{esc(row["url"])}">{esc(row["url"].split("//")[-1][:60])}</a>')
    buttons = [Button(f"{ICON_OK} Одобрить", cb(ACT_APPROVE, client.slug, search.slug, lid))]
    if row.get("brochure_url"):
        buttons.append(Button(f"{ICON_PDF} Брошюра", url=row["brochure_url"]))
    buttons.append(Button("⏭", cb(ACT_SKIP, client.slug, search.slug, lid)))
    return Outgoing(text="\n".join(lines), buttons=[buttons], photo_url=row.get("photo_url", ""),
                    meta={"client": client.slug, "search": search.slug, "listing": lid})


# ------------------------------------------------------------------ брифы

def client_draft_card(draft: dict) -> Outgoing:
    """Карточка нового клиента на проверку: «✅ Запустить · ✏️ Поправить»."""
    c, s = draft.get("client", {}), draft.get("search", {})
    lines = ["<b>Новый клиент — проверь:</b>",
             f"<b>{esc(c.get('name'))}</b>" + (f" · {esc(c.get('deadline_label') or c.get('deadline'))}" if c.get("deadline") else "")]
    lines += _search_lines(s)
    if draft.get("missing"):
        lines.append(f"<i>Не расслышал: {esc(', '.join(draft['missing']))} — добавишь позже.</i>")
    return Outgoing("\n".join(lines), [[
        Button(f"{ICON_OK} Запустить", cb(ACT_LAUNCH, draft["id"])),
        Button("✏️ Поправить", cb(ACT_EDIT, draft["id"])),
    ]])


def search_draft_card(client: Client, draft: dict, active: list[Search]) -> Outgoing:
    """Новый подбор у существующего клиента: заменить один из активных или добавить."""
    s = draft.get("search", {})
    lines = [tagline(client), "<b>Новый подбор:</b>"] + _search_lines(s)
    rows: list[list[Button]] = []
    if active:
        names = ", ".join(f"<b>{esc(a.title)}</b>" for a in active)
        lines.append(f"\nУ клиента уже активно: {names}. Что с ними?")
        for a in active:
            rows.append([Button(f"🔁 Заменить {a.title}"[:60], cb(ACT_REPLACE, draft["id"], a.slug))])
    rows.append([Button("➕ Добавить параллельно", cb(ACT_ADD, draft["id"])),
                 Button("✖ Отмена", cb(ACT_CANCEL, draft["id"]))])
    return Outgoing("\n".join(lines), rows)


def _search_lines(s: dict) -> list[str]:
    icon = "🏗" if s.get("market") == "offplan" else "🏠"
    what = "От застройщика" if s.get("market") == "offplan" else "Вторичка"
    beds = "/".join(f"{b}BR" for b in s.get("bedrooms", [])) or "—"
    budget = s.get("budget") or {}
    money_part = ""
    if budget.get("min") and budget.get("max"):
        money_part = f"{money(budget['min'])}–{money(budget['max'])} AED"
    elif budget.get("max"):
        money_part = f"до {money(budget['max'])} AED"
    lines = [f"{icon} {what} · <b>{esc(s.get('target') or s.get('title'))}</b> · {beds}"
             + (f" · <b>{money_part}</b>" if money_part else "")]
    if s.get("readiness") or s.get("handover"):
        lines.append(esc(" · ".join(x for x in (s.get("readiness"), s.get("handover")) if x)))
    if s.get("wishes"):
        lines.append("Пожелания: " + esc(", ".join(s["wishes"])))
    if s.get("stop"):
        lines.append("Стоп: " + esc(", ".join(s["stop"])))
    return lines


# ------------------------------------------------------------------ сводки

def first_collection_card(client: Client, search: Search, report: dict, sheet_link: str) -> Outgoing:
    lines = [tagline(client, search), f"<b>Собрано</b> · {report.get('total', 0)} объектов"]
    parts = []
    if report.get("ru"):
        parts.append(f"<b>{report['ru']} {ICON_RU}</b>")
    if report.get("in_budget") is not None:
        parts.append(f"в бюджет <b>{report['in_budget']}</b>")
    if parts:
        lines.append(" · ".join(parts))
    for beds, med in sorted((report.get("medians") or {}).items()):
        lines.append(f"Медиана {beds}BR: {money(med)} AED/м²")
    if report.get("best_delta") is not None:
        lines.append(f"Лучшая цена: <b>{report['best_delta']:+.0f} % к медиане</b>")
    if report.get("distress"):
        lines.append(f"{ICON_DISTRESS} Дистресс под бриф: {report['distress']}")
    if report.get("note"):
        lines.append(f"\n<i>{esc(report['note'])}</i>")
    return Outgoing("\n".join(lines), [[
        Button("📊 Книга", url=sheet_link),
        Button(f"{ICON_NEW} Карточки", cb(ACT_SHOW_NEW, client.slug, search.slug)),
    ]])


def search_digest(client: Client, search: Search, rep: dict, sheet_link: str) -> Outgoing:
    """Блок утреннего дайджеста по одному подбору в теме клиента."""
    lines = [tagline(client, search)]
    if rep.get("new"):
        ru = sum(1 for r in rep["new"] if r.get("ru_score") == 3)
        lines.append(f"{ICON_NEW} <b>{len(rep['new'])} новых</b>" + (f", {ru} у русскоязычных" if ru else ""))
    if rep.get("price"):
        deltas = ", ".join(f"{(r['new'] - r['old']):+,}".replace(",", " ") for r in rep["price"][:3])
        lines.append(f"{ICON_PRICE} {len(rep['price'])} сменили цену: {deltas}")
    if rep.get("removed"):
        note = " — среди них одобренный!" if rep.get("approved_removed") else " — не из одобренных"
        lines.append(f"{ICON_REMOVED} {len(rep['removed'])} снят{'о' if len(rep['removed']) != 1 else ''}{note}")
    if rep.get("distress"):
        lines.append(f"{ICON_DISTRESS} Дистресс: {esc(rep['distress'])}")
    if rep.get("changed"):
        lines.append(f"🔁 {len(rep['changed'])} проектов изменили условия")
    if len(lines) == 1:
        lines.append("Без изменений.")
    buttons: list[Button] = []
    if rep.get("new"):
        buttons.append(Button(f"{ICON_NEW} Показать новые", cb(ACT_SHOW_NEW, client.slug, search.slug)))
    if rep.get("price"):
        buttons.append(Button(f"{ICON_PRICE} Цены", cb(ACT_SHOW_PRICES, client.slug, search.slug)))
    buttons.append(Button("📊 Лист", url=sheet_link))
    return Outgoing("\n".join(lines), [buttons])


def general_digest(rows: list[dict], paused: list[str], deadlines: list[str]) -> Outgoing:
    """Одна строка на подбор в General."""
    active = [r for r in rows if r.get("changed")]
    quiet = sorted({r["client"] for r in rows if not r.get("changed")})
    lines = [f"<b>Утро · {len({r['client'] for r in rows})} активных клиентов</b>\n"]
    for r in active:
        bits = []
        if r.get("new"):
            bits.append(f"{ICON_NEW} {r['new']}")
        if r.get("price"):
            bits.append(f"{ICON_PRICE} {r['price']}")
        if r.get("removed"):
            bits.append(f"{ICON_REMOVED} {r['removed']}")
        if r.get("distress"):
            bits.append(f"{ICON_DISTRESS} {r['distress']}")
        if r.get("changed_projects"):
            bits.append(f"🔁 {r['changed_projects']}")
        lines.append(f"{r['icon']} <b>{esc(r['client'])}</b> · {esc(r['search'])} · " + " · ".join(bits))
    if quiet:
        lines.append(f"Без изменений: {esc(', '.join(quiet))}")
    if paused:
        lines.append(f"⏸ На паузе: {esc(', '.join(paused))}")
    if deadlines:
        lines.append(f"\n<i>{esc('; '.join(deadlines))}</i>")
    if not rows:
        lines.append("Активных подборов нет.")
    return Outgoing("\n".join(lines), [[Button("/clients", cb(ACT_CLIENTS))]])


def clients_list(items: list[dict]) -> Outgoing:
    active = [i for i in items if i["status"] == "active"]
    archived = [i for i in items if i["status"] != "active"]
    lines = [f"<b>Активные · {len(active)}</b>"]
    for i in active:
        lines.append(f"{i['icon']} {esc(i['name'])} · {i['searches']} подбор{_pl(i['searches'])}"
                     f" · {i['approved']} {ICON_OK}" + (f" · сделка {esc(i['deadline'])}" if i.get("deadline") else ""))
    if archived:
        lines.append(f"<b>Архив · {len(archived)}</b> · " + esc(", ".join(i["name"] for i in archived)))
    if not items:
        lines.append("Клиентов пока нет. Надиктуй бриф в General — заведу.")
    return Outgoing("\n".join(lines), [])


def _pl(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return ""
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "а"
    return "ов"


# ------------------------------------------------------------------ действия

def whatsapp_card(client: Client, search: Search, row: dict, link: str) -> Outgoing:
    lid = row.get("id", "")
    lines = [tagline(client, search),
             f"<b>{esc(row.get('agent_name'))} · {esc(row.get('agency'))}</b>",
             f"{esc(row.get('bedrooms'))}BR · {esc(row.get('size_m2'))} м² · {money(row.get('price'))}"
             + (f" · ref {esc(row['reference'])}" if row.get("reference") else ""),
             "Ссылка свежая. Текст Property Finder готов — <b>не редактируй</b>, иначе до агента не дойдёт. "
             "Свои вопросы — уже в ответной переписке."]
    return Outgoing("\n".join(lines), [
        [Button("💬 Открыть WhatsApp", url=link)],
        [Button(f"{ICON_SENT} Отправил", cb(ACT_SENT, client.slug, search.slug, lid)),
         Button("✖ Передумал", cb(ACT_CANCEL, client.slug, search.slug, lid))],
    ], meta={"client": client.slug, "search": search.slug, "listing": lid})


def ask_price_card(client: Client, search: Search, row: dict) -> Outgoing:
    lines = [tagline(client, search),
             f"<b>{esc(row.get('bedrooms'))}BR · {esc(row.get('size_m2'))} м² · {esc(row.get('agency'))}</b>",
             f"В объявлении {money(row.get('price'))}. <b>Твоя цена для клиента?</b>",
             "<i>Из объявления не подставлю — только то, что назовёшь. Ответь числом.</i>"]
    return Outgoing("\n".join(lines), [[Button("✖ Отмена", cb(ACT_CANCEL, client.slug, search.slug, row.get("id", "")))]])


def pdf_card(client: Client, search: Search, result: dict) -> Outgoing:
    lines = [tagline(client, search),
             f"{ICON_OK} Одобрено · Моя цена <b>{money(result.get('price'))}</b> — записал в лист от твоего имени."]
    if result.get("summary"):
        lines.append(esc(result["summary"]))
    if result.get("price") and result.get("usd"):
        lines.append(f"<b>{money(result['price'])} AED ≈ {money(result['usd'])} USD</b>")
    if result.get("link"):
        lines.append(f'<a href="{esc(result["link"])}">Ссылка на Диске — в колонке «Презентация»</a>')
    buttons = []
    if result.get("folder_link"):
        buttons.append(Button("📂 Папка", url=result["folder_link"]))
    buttons.append(Button("🔁 Пересобрать", cb(ACT_REBUILD, client.slug, search.slug, result.get("listing", ""))))
    return Outgoing("\n".join(lines), [buttons], file_path=result.get("pdf_path", ""))


def confirm_card(text: str, action: str, client: str = "", search: str = "", listing: str = "") -> Outgoing:
    return Outgoing(text, [[Button(f"{ICON_SENT} Подтверждаю", cb(action, client, search, listing)),
                            Button("✖ Отмена", cb(ACT_CANCEL, client, search, listing))]])


def warn(text: str) -> Outgoing:
    return Outgoing(f"{ICON_WARN} {text}")


def plain(text: str) -> Outgoing:
    return Outgoing(text)
