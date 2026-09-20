"""Инструменты агента: объекты подборов и рынок.

Данные — только из книг и выгрузок; агент из них отвечает на вопросы Алексея
и показывает карточки. Кнопки под карточками обрабатывает bot.py.
"""

from __future__ import annotations

import json

from langchain.tools import tool

from lib import listing_detail, propertyfinder
from scout import actions, cards, outbox
from scout.models import MARKET_SECONDARY

QUERY_COLUMNS = ("listing_id", "Тип", "Площадь м²", "Цена AED", "AED/м²", "К рынку", "Вид на воду",
                 "🇷🇺", "Брокер", "Агентство", "✅ Одобрено", "📩 Запросить", "Запрошено", "Моя цена",
                 "Статус", "Презентация", "Серия", "Комментарий системы", "Ссылка")
OFFPLAN_COLUMNS = ("listing_id", "Балл", "Проект", "Застройщик", "Район", "Фаза продаж", "Старт продаж",
                   "Сдача", "Тип", "Цена от AED", "Площадь м²", "AED/м²", "К району", "К DLD", "План оплаты",
                   "Взнос %", "✅ Одобрено", "Статус", "Брошюра", "Презентация", "Комментарий системы", "Ссылка")


def _pair(client: str, search: str):
    c = actions.find_client(client)
    if c is None:
        raise ValueError(f"Клиент «{client}» не найден — уточни фамилию или slug")
    searches = actions.client_searches(c)
    if not search:
        active = [s for s in searches if s.status == "active"]
        if len(active) != 1:
            names = ", ".join(f"{s.slug} («{s.title}»)" for s in active) or "нет активных"
            raise ValueError(f"У клиента несколько подборов — укажи search: {names}")
        return c, active[0]
    for s in searches:
        if s.slug == search or s.title.lower() == search.lower():
            return c, s
    raise ValueError(f"Подбор «{search}» у {c.name} не найден: " + ", ".join(s.slug for s in searches))


@tool
def sheet_rows(client: str, search: str = "", limit: int = 120) -> str:
    """Строки листа подбора — чтобы ответить на вопрос Алексея по данным, не выдумывая.

    Args:
        client: slug или фамилия клиента.
        search: slug или название подбора; пусто — единственный активный.
        limit: сколько строк вернуть сверху листа (по умолчанию 120).

    Returns:
        JSON-строки: одна строка листа — один объект с ключами-колонками. Первая строка —
        тег «Клиент · Подбор», его нужно ставить в начало ответа.
    """
    c, s = _pair(client, search)
    rows = actions.sheet_rows(c, s)[:limit]
    columns = OFFPLAN_COLUMNS if s.market != MARKET_SECONDARY else QUERY_COLUMNS
    slim = [{k: v for k, v in r.items() if k in columns and v} for r in rows]
    return f"{c.short_name} · {s.title} · строк {len(rows)}\n" + json.dumps(slim, ensure_ascii=False)


@tool
def show_new(client: str, search: str = "", count: int = 3) -> str:
    """Показать Алексею карточки новых объектов подбора (фото, факты, кнопки ✅ 📩 ⏭).

    Args:
        client: slug или фамилия клиента.
        search: slug подбора; пусто — единственный активный.
        count: сколько карточек за раз, 1–5.

    Returns:
        Сколько карточек отправлено и сколько новых осталось.
    """
    c, s = _pair(client, search)
    pending = actions.pending_cards(c, s)
    batch = pending[:max(1, min(count, 5))]
    for i, row in enumerate(batch, start=1):
        enriched = actions.enrich_for_card(s, row)
        card = (cards.listing_card if s.market == MARKET_SECONDARY else cards.project_card)(
            c, s, enriched, position=f"{i} из {len(pending)}")
        outbox.push(card)
    actions.mark_shown(s, [r["id"] for r in batch])
    if not batch:
        return "Новых непоказанных объектов в этом подборе нет."
    return f"Отправлено карточек: {len(batch)}; новых осталось: {len(pending) - len(batch)}."


@tool
def show_objects(client: str, search: str, listing_ids: list[str]) -> str:
    """Показать карточки конкретных объектов по их listing_id (после ответа на вопрос).

    Args:
        client: slug или фамилия клиента.
        search: slug подбора.
        listing_ids: до 5 идентификаторов из колонки listing_id, например ["PF-141439708"].

    Returns:
        Сколько карточек отправлено; каких объектов нет в выгрузке.
    """
    c, s = _pair(client, search)
    sent, missing = 0, []
    for lid in listing_ids[:5]:
        try:
            row = actions.raw_row(s, lid)
        except actions.NotFound:
            missing.append(lid)
            continue
        enriched = actions.enrich_for_card(s, row)
        card = (cards.listing_card if s.market == MARKET_SECONDARY else cards.project_card)(c, s, enriched)
        outbox.push(card)
        sent += 1
    return f"Карточек отправлено: {sent}." + (f" Нет в выгрузке: {', '.join(missing)}" if missing else "")


@tool
def object_details(client: str, search: str, listing_id: str) -> str:
    """Подробности объявления с Property Finder: описание, фото, планировки, сделки DLD рядом.

    Args:
        client: slug или фамилия клиента.
        search: slug подбора.
        listing_id: идентификатор объекта, например «PF-141439708».

    Returns:
        Текст с фактами объявления. Вид и этаж бери из заголовка, описание — запасной источник.
    """
    c, s = _pair(client, search)
    row = actions.raw_row(s, listing_id)
    d = listing_detail.detail_or_row(row)
    parts = [f"{c.short_name} · {s.title} · {listing_id}",
             f"Заголовок: {d.get('title', '')}",
             f"{d.get('bedrooms')}BR · {d.get('size_m2')} м² · {row.get('price')} AED · {row.get('price_per_m2')} AED/м²",
             f"Фото: {len(d.get('images') or [])}, планировок размечено: {len(d.get('floor_plans') or [])}",
             f"Агент: {row.get('agent_name')} · {row.get('agency')} · языки: {row.get('agent_languages')}"]
    if d.get("description"):
        parts.append("Описание: " + d["description"][:800])
    if d.get("amenities"):
        parts.append("Удобства: " + ", ".join(d["amenities"][:15]))
    if d.get("transactions"):
        parts.append("Сделки DLD: " + json.dumps(d["transactions"][:6], ensure_ascii=False))
    return "\n".join(parts)


@tool
def pf_probe(url: str) -> str:
    """Проверить страницу башни или района на propertyfinder.ae: сколько там объявлений.

    Используй, чтобы подобрать source_url для нового подбора: адрес вида
    https://www.propertyfinder.ae/en/buy/dubai/apartments-for-sale-<район>-<башня>.html.
    Текстовый поиск PF не годится — он отдаёт весь Дубай.

    Args:
        url: полный адрес страницы каталога.

    Returns:
        Число объявлений и три первых заголовка, или причину, почему страница не подходит.
    """
    if "propertyfinder.ae" not in url:
        return "Только propertyfinder.ae."
    try:
        listings, total = propertyfinder.parse_page(propertyfinder.fetch(url))
    except Exception as error:  # noqa: BLE001
        return f"Страница не открылась: {type(error).__name__}: {error}"
    if not listings:
        return "Объявлений на странице нет — адрес неверный или раздел пуст."
    titles = [(propertyfinder.normalize(x) or {}).get("title", "") for x in listings[:3]]
    if total > 5000:
        return f"На странице {total} объявлений — это весь Дубай, адрес не про башню/район."
    return f"Объявлений: {total}. Примеры: " + " | ".join(t for t in titles if t)


@tool
def dld_queue(client: str, search: str = "", limit: int = 10) -> str:
    """Прислать Алексею ссылки на карты DLD по объектам подбора — для контактов брокеров и точной площади.

    Карта DLD за капчей, читает её Алексей в своём браузере: открывает ссылку, отвечает
    скриншотом — бот записывает телефон, email, площадь, срок разрешения и дубли ✔.
    Сначала одобренные и запрошенные, потом по порядку листа; уже проверенные пропускаются.

    Args:
        client: slug или фамилия клиента.
        search: slug подбора; пусто — единственный активный.
        limit: сколько ссылок за раз, 1–20.

    Returns:
        Сколько ссылок отправлено.
    """
    from scout import dld

    c, s = _pair(client, search)
    items = dld.queue(c, s, limit=max(1, min(limit, 20)))
    for i, item in enumerate(items, start=1):
        outbox.push(cards.dld_link_card(c, s, item, position=f"{i} из {len(items)}"))
    if not items:
        return "Все объекты подбора уже с картами DLD или ссылок на карты нет."
    return f"Отправлено ссылок на карты DLD: {len(items)}. Алексей отвечает скриншотом на каждое."


TOOLS = [sheet_rows, show_new, show_objects, object_details, pf_probe, dld_queue]
