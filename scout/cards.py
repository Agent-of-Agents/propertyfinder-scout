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
ACT_DELETE, ACT_DELETE_CONFIRM, ACT_MANAGE = "dl", "dd", "mg"
ACT_REM_DONE, ACT_REM_SNOOZE3, ACT_REM_SNOOZE7, ACT_REM_CANCEL = "rd", "r3", "r7", "rx"   # напоминания
ACT_DLD_QUEUE, ACT_DLD_PICK = "dq", "dp"
ACT_COMMENT = "cm"                                                                         # 💬 заметка по объекту                                                   # карты DLD      # удалить полностью: карточка → подтверждение


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
    if row.get("dupes"):
        lines.append(f"≈ Та же квартира ещё у {row['dupes']} брокер{'а' if row['dupes'] in (1, 2, 3, 4) else 'ов'} — см. «Дубли» в листе")
    if row.get("comment"):
        lines.append(f"<i>{esc(row['comment'])}</i>")
    if row.get("url"):
        lines.append(f'<a href="{esc(row["url"])}">{esc(row["url"].split("//")[-1][:60])}</a>')
    # 📩 — сразу объявление на PF: там кнопка WhatsApp с готовым текстом (шлюз PF закрыт
    # для скриптов с 09.2026, см. память). «✔ Отправил» ставит дату в «Запрошено».
    request = (Button(f"{ICON_REQ} WhatsApp на PF", url=row["url"]) if row.get("url")
               else Button(f"{ICON_REQ} Запросить", cb(ACT_REQUEST, client.slug, search.slug, lid)))
    return Outgoing(
        text="\n".join(lines),
        buttons=[
            [Button(f"{ICON_OK} Одобрить", cb(ACT_APPROVE, client.slug, search.slug, lid)), request,
             Button("⏭", cb(ACT_SKIP, client.slug, search.slug, lid))],
            [Button(f"{ICON_SENT} Отправил брокеру", cb(ACT_SENT, client.slug, search.slug, lid)),
             Button("💬 Комментарий", cb(ACT_COMMENT, client.slug, search.slug, lid))],
        ],
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
    buttons.append(Button("💬", cb(ACT_COMMENT, client.slug, search.slug, lid)))
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
        Button("🪪 DLD", cb(ACT_DLD_QUEUE, client.slug, search.slug)),
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
    buttons.append(Button("🪪 DLD", cb(ACT_DLD_QUEUE, client.slug, search.slug)))
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


def whatsapp_fallback_card(client: Client, search: Search, row: dict, reason: str) -> Outgoing:
    """Шлюз PF с сервера закрыт — WhatsApp жмёт Алексей на странице объявления."""
    lid = row.get("id", "")
    lines = [tagline(client, search),
             f"<b>{esc(row.get('agent_name'))} · {esc(row.get('agency'))}</b> · {money(row.get('price'))} AED",
             "На PF нажми WhatsApp — текст подставится сам, не редактируй. Потом «Отправил»."]
    if reason:
        lines.append(f"⚠️ {esc(reason)}")
    rows = []
    if row.get("url"):
        rows.append([Button("📩 WhatsApp на PF", url=row["url"])])
    rows.append([Button(f"{ICON_SENT} Отправил", cb(ACT_SENT, client.slug, search.slug, lid)),
                 Button("✖ Передумал", cb(ACT_CANCEL, client.slug, search.slug, lid))])
    return Outgoing("\n".join(lines), rows, meta={"client": client.slug, "search": search.slug, "listing": lid})


def ask_comment_card(client: Client, search: Search, row: dict) -> Outgoing:
    """«💬 Комментарий»: ждём голосовое или текст — запишем в «Мой комментарий»."""
    lines = [tagline(client, search),
             f"<b>{esc(row.get('agent_name') or row.get('project') or '')}</b> · {money(row.get('price'))}",
             "Слушаю: голосовое или текст следующим сообщением — запишу в <b>«Мой комментарий»</b> этой строки."]
    return Outgoing("\n".join(lines), [[Button("✖ Отмена", cb(ACT_CANCEL, client.slug, search.slug, row.get("id", "")))]],
                    meta={"client": client.slug, "search": search.slug, "listing": row.get("id", "")})


def comment_saved_card(client: Client, search: Search, row: dict, text: str, stamp: str) -> Outgoing:
    lines = [tagline(client, search),
             f"💬 <b>Записал в «Мой комментарий»</b> · {esc(row.get('agent_name') or row.get('project') or '')}"
             f" · {money(row.get('price'))}",
             f"<i>{esc(stamp)} · {esc(text)}</i>"]
    return Outgoing("\n".join(lines), [[Button("💬 Ещё", cb(ACT_COMMENT, client.slug, search.slug, row.get("id", "")))]],
                    meta={"client": client.slug, "search": search.slug, "listing": row.get("id", "")})


def ask_price_card(client: Client, search: Search, row: dict) -> Outgoing:
    lines = [tagline(client, search),
             f"<b>{esc(row.get('bedrooms'))}BR · {esc(row.get('size_m2'))} м² · {esc(row.get('agency'))}</b>",
             f"В объявлении {money(row.get('price'))}. <b>Твоя цена для клиента?</b>",
             "<i>Из объявления не подставлю — только то, что назовёшь. Ответь числом.</i>"]
    return Outgoing("\n".join(lines), [[Button("✖ Отмена", cb(ACT_CANCEL, client.slug, search.slug, row.get("id", "")))]])


def pdf_card(client: Client, search: Search, result: dict) -> Outgoing:
    head = (f"{ICON_OK} Одобрено · цена застройщика <b>от {money(result.get('price'))}</b> — презентация собрана."
            if result.get("from_price")
            else f"{ICON_OK} Одобрено · Моя цена <b>{money(result.get('price'))}</b> — записал в лист от твоего имени.")
    lines = [tagline(client, search), head]
    if result.get("summary"):
        lines.append(esc(result["summary"]))
    if result.get("price") and result.get("usd"):
        lines.append(f"<b>{money(result['price'])} AED ≈ {money(result['usd'])} USD</b>")
    if result.get("media"):
        lines.append(f"<i>{esc(result['media'])}</i>")
    if result.get("link"):
        lines.append(f'<a href="{esc(result["link"])}">Ссылка на Диске — в колонке «Презентация»</a>')
    buttons = []
    if result.get("brochure"):
        buttons.append(Button(f"{ICON_PDF} Брошюра застройщика", url=result["brochure"]))
    if result.get("folder_link"):
        buttons.append(Button("📂 Папка", url=result["folder_link"]))
    buttons.append(Button("🔁 Пересобрать", cb(ACT_REBUILD, client.slug, search.slug, result.get("listing", ""))))
    return Outgoing("\n".join(lines), [buttons], file_path=result.get("pdf_path", ""))


def brochure_card(client: Client, search: Search, result: dict) -> Outgoing:
    """Off-plan одобрен: брошюра застройщика файлом, ссылка на Диске — в «Презентации»."""
    lines = [tagline(client, search),
             f"{ICON_OK} Одобрено · <b>{esc(result.get('summary', ''))}</b>",
             f"от {money(result.get('price'))} AED"
             + (f" ≈ {money(result.get('usd'))} USD" if result.get("usd") else "")]
    if result.get("missing"):
        lines.append(f"{ICON_WARN} {esc(result['missing'])}")
    else:
        size = f" · {result['size_mb']} МБ" if result.get("size_mb") else ""
        lines.append(f"{ICON_PDF} <b>Брошюра застройщика</b>{size} — файлом ниже, копия на Диске "
                     "в колонке «Презентация».")
    buttons = []
    if result.get("brochure"):
        buttons.append(Button(f"{ICON_PDF} Брошюра на PF", url=result["brochure"]))
    if result.get("folder_link"):
        buttons.append(Button("📂 Папка", url=result["folder_link"]))
    return Outgoing("\n".join(lines), [buttons] if buttons else [],
                    file_path=result.get("pdf_path", ""),
                    meta={"client": client.slug, "search": search.slug, "listing": result.get("listing", "")})


def client_fate_card(client: Client, searches: list, note: str = "") -> Outgoing:
    """Что делать с клиентом: заморозить, куплено, в архив, удалить. Решает Алексей кнопкой."""
    live = [s for s in searches if s.status in ("active", "paused")]
    lines = [f"<b>{esc(client.name)}</b> · {esc(client.status)}"]
    if live:
        lines.append("Подборы: " + ", ".join(f"{s.icon} {esc(s.title)}" + (" ⏸" if s.status == "paused" else "") for s in live))
    else:
        lines.append("Активных подборов нет.")
    if note:
        lines.append(f"<i>{esc(note)}</i>")
    lines.append("\nЧто с ним делаем?")
    rows = [
        [Button("⏸ Заморозить", cb(ACT_PAUSE, client.slug)), Button("✓ Куплено", cb(ACT_CONFIRM, client.slug, "", "bought"))],
        [Button("✕ В архив", cb(ACT_CONFIRM, client.slug, "", "dropped")), Button("🗑 Удалить полностью", cb(ACT_DELETE, client.slug))],
    ]
    if len(live) > 1:
        for s in live:
            rows.append([Button(f"✓ Куплено · {s.title}"[:60], cb(ACT_CONFIRM, client.slug, s.slug, "bought")),
                         Button(f"✕ Закрыть · {s.title}"[:60], cb(ACT_CONFIRM, client.slug, s.slug, "dropped"))])
    rows.append([Button("✖ Отмена", cb(ACT_CANCEL))])
    return Outgoing("\n".join(lines), rows)


def delete_confirm_card(client: Client, searches: list) -> Outgoing:
    text = (f"🗑 <b>Удалить {esc(client.name)} полностью?</b>\n"
            f"Подборов: {len(searches)}. Карточка клиента и состояние сверок исчезнут, "
            "книга уйдёт в корзину Диска (30 дней можно вернуть), тема в группе удалится. "
            "Папка с планировками на Диске останется.\n\n"
            "Если клиент просто «на паузе» или «купил» — лучше ⏸ или ✓: история сохранится.")
    return Outgoing(text, [[Button("🗑 Да, удалить", cb(ACT_DELETE_CONFIRM, client.slug)),
                            Button("✖ Отмена", cb(ACT_CANCEL))]])


def reminder_card(client: Client | None, rem: dict, summary: str = "") -> Outgoing:
    """Напоминание в срок: текст, сводка по клиенту, ✔ Сделано · ⏰ +3 · ⏰ +7."""
    head = tagline(client) if client else ""
    lines = [head + "⏰ <b>Напоминание</b>", esc(rem.get("text", ""))]
    if summary:
        lines.append(f"<i>{esc(summary)}</i>")
    rid = rem["id"]
    return Outgoing("\n".join(lines), [[
        Button("✔ Сделано", cb(ACT_REM_DONE, rid)),
        Button("⏰ +3 дня", cb(ACT_REM_SNOOZE3, rid)),
        Button("⏰ +7 дней", cb(ACT_REM_SNOOZE7, rid)),
    ]])


def reminder_set_card(client: Client | None, rem: dict, when_label: str) -> Outgoing:
    head = tagline(client) if client else ""
    return Outgoing(head + f"⏰ Напомню <b>{esc(when_label)}</b>: {esc(rem.get('text', ''))}",
                    [[Button("✖ Отменить", cb(ACT_REM_CANCEL, rem["id"]))]])


def reminders_list_card(items: list[tuple[dict, str, str]]) -> Outgoing:
    """items: (напоминание, имя клиента, подпись срока)."""
    if not items:
        return Outgoing("Напоминаний нет. Напиши, например: «напомни в четверг коснуться Гареева».")
    lines = [f"<b>⏰ Напоминания · {len(items)}</b>"]
    rows = []
    for rem, who, when in items:
        lines.append(f"• {esc(when)} · <b>{esc(who)}</b> — {esc(rem.get('text', ''))}")
        rows.append([Button(f"✖ {when} · {who}"[:60], cb(ACT_REM_CANCEL, rem["id"]))])
    return Outgoing("\n".join(lines), rows)


def followup_card(client: Client, silent_days: int, news: str, searches_line: str) -> Outgoing:
    """Клиент затих: что накопилось и что делать."""
    lines = [tagline(client),
             f"⏳ <b>{esc(client.short_name)} молчит {silent_days} дн.</b> — с последнего касания:",
             esc(news) if news else "новых объектов не появилось",
             esc(searches_line)]
    return Outgoing("\n".join(lines), [
        [Button(f"{ICON_NEW} Показать что нового", cb(ACT_SHOW_NEW, client.slug))],
        [Button("⏰ Напомнить через неделю", cb(ACT_REM_SNOOZE7, f"fu:{client.slug}")),
         Button("⏸ Заморозить", cb(ACT_PAUSE, client.slug))],
    ])


def dld_link_card(client: Client, search: Search, item: dict, position: str = "") -> Outgoing:
    """Одна ссылка на карту DLD. Ответ на это сообщение скриншотом или текстом карты — запись в лист."""
    lines = [tagline(client, search, position),
             f"🪪 <b>{esc(item.get('agent') or '—')}</b> · {esc(item.get('agency') or '')}".rstrip(" ·"),
             f"{money(item.get('price'))} AED · {esc(item.get('building') or '')}".rstrip(" ·")]
    if item.get("permit_url"):
        lines.append("Открой карту, сделай <b>скриншот</b> и ответь им на это сообщение. Или выдели текст карты и вставь ответом.")
    else:
        lines.append("⚠️ У объявления нет ссылки на карту DLD — разрешение не указано.")
    rows = []
    if item.get("permit_url"):
        rows.append([Button("🪪 Открыть карту DLD", url=item["permit_url"])])
    if item.get("url"):
        rows.append([Button("🔗 Объявление", url=item["url"])])
    return Outgoing("\n".join(lines), rows,
                    meta={"client": client.slug, "search": search.slug, "listing": item["listing_id"], "kind": "dld"})


def dld_written_card(client: Client, search: Search, result: dict, agent: str) -> Outgoing:
    card = result.get("card", {})
    bits = []
    if card.get("size_sqm"):
        bits.append(f"<b>{card['size_sqm']:.2f} м²</b>")
    if card.get("broker_mobile"):
        bits.append(f"📱 {esc(card['broker_mobile'])}")
    if card.get("broker_email"):
        bits.append(f"✉️ {esc(card['broker_email'])}")
    if card.get("permit_until"):
        bits.append(f"разрешение до {esc(card['permit_until'])}")
    lines = [tagline(client, search), f"🪪 Записано · <b>{esc(agent)}</b>", " · ".join(bits) or "поля не распознаны"]
    if result.get("dupes"):
        lines.append(f"✔ Та же квартира ещё у {len(result['dupes'])} — отмечено в «Дубли»")
    return Outgoing("\n".join(lines))


def dld_pick_card(client: Client, search: Search, candidates: list[dict], token: str) -> Outgoing:
    """Карта пришла без ответа на сообщение и не сошлась однозначно — спросим, чья."""
    rows = [[Button(f"{c.get('agent') or c['listing_id']} · {money(c.get('price'))}"[:60],
                    cb(ACT_DLD_PICK, client.slug, search.slug, f"{token}:{c['listing_id']}"))] for c in candidates[:8]]
    rows.append([Button("✖ Отмена", cb(ACT_CANCEL))])
    return Outgoing(tagline(client, search) + "К какому объекту эта карта DLD?", rows)


def confirm_card(text: str, action: str, client: str = "", search: str = "", listing: str = "") -> Outgoing:
    return Outgoing(text, [[Button(f"{ICON_SENT} Подтверждаю", cb(action, client, search, listing)),
                            Button("✖ Отмена", cb(ACT_CANCEL, client, search, listing))]])


def warn(text: str) -> Outgoing:
    return Outgoing(f"{ICON_WARN} {text}")


def plain(text: str) -> Outgoing:
    return Outgoing(text)
