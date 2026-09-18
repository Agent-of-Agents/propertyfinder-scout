"""Инструменты агента: клиенты и подборы.

Тонкие обёртки над scout.actions. Всё необратимое — создание клиента, замена
подбора — идёт через черновик и кнопку в Telegram: агент готовит карточку,
подтверждает Алексей. Инструмент возвращает модели короткий текст; карточка
с кнопками уходит в scout.outbox, оттуда её отправляет bot.py.
"""

from __future__ import annotations

from langchain.tools import tool

from scout import actions, books, cards, outbox
from scout.store import STATES, get_store


def _client(hint: str):
    client = actions.find_client(hint)
    if client is None:
        raise ValueError(f"Клиент «{hint}» не найден или найдено несколько — уточни фамилию")
    return client


@tool
def list_clients() -> str:
    """Список клиентов Scout Dubai: имя, slug, статус, активные подборы, дата сделки.

    Returns:
        Строки «slug · Имя · статус · N подборов · сделка дд.мм.гггг». Пусто — клиентов нет.
    """
    items = actions.clients_overview()
    if not items:
        return "Клиентов пока нет."
    return "\n".join(
        f"{i['slug']} · {i['name']} · {i['status']} · {i['searches']} активных подборов"
        + (f" · сделка {i['deadline']}" if i.get("deadline") else "")
        for i in items
    )


@tool
def client_overview(client: str) -> str:
    """Подборы клиента с цифрами и ссылкой на его книгу Google.

    Args:
        client: slug или фамилия клиента, например «ivanova» или «Иванова».

    Returns:
        По каждому подбору: название, рынок, статус, объектов, одобрено, запрошено,
        презентаций, дата последнего прогона. Плюс ссылка на книгу.
    """
    c = _client(client)
    store = get_store()
    lines = [f"{c.name} ({c.slug}) · статус {c.status}" + (f" · сделка {c.deadline}" if c.deadline else "")]
    for s in actions.client_searches(c):
        state = store.get(STATES, s.key) or {}
        stats = state.get("stats") or {}
        last = (state.get("last_report") or {}).get("run_at", "—")
        lines.append(
            f"- {s.icon} «{s.title}» [{s.slug}] · {s.market} · {s.status}"
            + (f" ({s.close_reason})" if s.close_reason else "")
            + f" · объектов {stats.get('objects', '?')} · ✅ {stats.get('approved', 0)}"
            f" · 📩 {stats.get('requested', 0)} · PDF {stats.get('pdf', 0)} · прогон {last}"
        )
    if c.spreadsheet_id:
        lines.append(f"Книга: {books.sheet_url(c.spreadsheet_id)}")
    return "\n".join(lines)


@tool
def propose_client(
    name: str,
    market: str,
    target: str,
    bedrooms: list[str],
    budget_max: int,
    budget_min: int = 0,
    source_url: str = "",
    contact: str = "",
    deadline: str = "",
    readiness: str = "",
    handover: str = "",
    wishes: list[str] | None = None,
    stop: list[str] | None = None,
    notes: str = "",
    missing: list[str] | None = None,
) -> str:
    """Карточка нового клиента с первым подбором — Алексею на проверку, с кнопкой «Запустить».

    Вызывай, когда из брифа понятны имя, рынок, локация или проект, спальни и бюджет.
    Ничего не создаёт: клиент, книга и тема появятся только после кнопки.

    Args:
        name: имя клиента, «Фамилия Имя».
        market: «secondary» — вторичка и перепродажи (объявления PF); «offplan» — проекты застройщиков.
        target: проект или район, как его назвал Алексей: «Marina Shores», «Downtown», «Business Bay».
        bedrooms: типы квартир строками: ["1", "2"]; студия — "studio".
        budget_max: потолок бюджета в AED.
        budget_min: нижняя граница в AED, 0 если не названа.
        source_url: для secondary — ссылка на страницу башни или района на propertyfinder.ae
            (проверь её инструментом pf_probe). Пусто — Алексею придёт просьба дать ссылку.
        contact: телефон или иной контакт клиента, если назван.
        deadline: дата сделки или приезда, ISO «2026-10-15», если названа.
        readiness: «готовая», «офф-план перепродажа» и т.п., если названо.
        handover: срок сдачи, если назван («Q4 2026»).
        wishes: пожелания списком: «вид на воду», «цена/качество».
        stop: стоп-лист: чего не предлагать.
        notes: контекст сделки одной-двумя фразами (наличные, приезд семьи и т.п.).
        missing: что не удалось расслышать/понять — покажется в карточке.

    Returns:
        Подтверждение, что карточка отправлена, и что делать дальше.
    """
    search = {
        "market": market, "target": target, "bedrooms": bedrooms,
        "budget": {k: v for k, v in (("min", budget_min), ("max", budget_max)) if v},
        "sources": [{"kind": "propertyfinder", "url": source_url}] if source_url else [],
        "readiness": readiness, "handover": handover,
        "wishes": wishes or [], "stop": stop or [],
    }
    draft = actions.save_draft("client", {
        "client": {"name": name, "contact": contact, "deadline": deadline, "notes": notes},
        "search": search,
        "missing": missing or ([] if source_url or market == "offplan" else ["ссылка на страницу башни/района PF"]),
    })
    outbox.push(cards.client_draft_card(draft))
    return (f"Карточка клиента «{name}» отправлена Алексею с кнопками «Запустить / Поправить». "
            f"Черновик {draft['id']}. Ничего не создано до кнопки.")


@tool
def propose_search(
    client: str,
    market: str,
    target: str,
    bedrooms: list[str],
    budget_max: int,
    budget_min: int = 0,
    source_url: str = "",
    title: str = "",
    readiness: str = "",
    handover: str = "",
    wishes: list[str] | None = None,
    stop: list[str] | None = None,
) -> str:
    """Новый подбор у существующего клиента — карточка с кнопками «Заменить … / Добавить параллельно».

    Клиент передумал или хочет ещё один объект — подбор не редактируется, а открывается
    новый; что делать со старыми, решает Алексей кнопкой. Ничего не создаёт до кнопки.

    Args:
        client: slug или фамилия клиента.
        market: «secondary» или «offplan».
        target: проект или район.
        bedrooms: типы квартир строками.
        budget_max: потолок в AED.
        budget_min: нижняя граница, 0 если нет.
        source_url: ссылка на страницу башни/района PF для secondary.
        title: имя листа, например «Downtown 1BR»; пусто — соберётся из target и спален.
        readiness: готовность объекта, если названа.
        handover: срок сдачи, если назван.
        wishes: пожелания.
        stop: стоп-лист.

    Returns:
        Подтверждение отправки карточки.
    """
    c = _client(client)
    search = {
        "market": market, "target": target, "title": title, "bedrooms": bedrooms,
        "budget": {k: v for k, v in (("min", budget_min), ("max", budget_max)) if v},
        "sources": [{"kind": "propertyfinder", "url": source_url}] if source_url else [],
        "readiness": readiness, "handover": handover, "wishes": wishes or [], "stop": stop or [],
    }
    draft = actions.save_draft("search", {"client_slug": c.slug, "search": search})
    active = actions.client_searches(c, active_only=True)
    outbox.push(cards.search_draft_card(c, draft, active))
    return (f"Карточка нового подбора для {c.name} отправлена. Активных подборов у клиента: "
            f"{len(active)} — Алексей выберет «Заменить» или «Добавить параллельно».")


@tool
def manage_client(client: str, intent: str = "") -> str:
    """Алексей просит убрать, заморозить, закрыть или удалить клиента — карточка с кнопками ему на решение.

    Ничего не меняет сам: заморозить (⏸ пауза прогона), «куплено» (✓ подборы закрываются,
    клиент в архив), «в архив» (✕ передумал), «удалить полностью» (🗑, с подтверждением) —
    выбирает Алексей кнопкой. Черновики карточек (клиент ещё не создан) удаляются
    инструментом discard_draft.

    Args:
        client: slug или фамилия клиента.
        intent: что сказал Алексей, своими словами — попадёт в карточку подсказкой:
            «купил», «передумал», «заморозить до октября», «удали».

    Returns:
        Подтверждение, что карточка отправлена.
    """
    c = _client(client)
    searches = actions.client_searches(c)
    outbox.push(cards.client_fate_card(c, searches, note=intent))
    return f"Карточка по {c.name} с кнопками отправлена: заморозить / куплено / в архив / удалить."


@tool
def discard_draft(client_name: str) -> str:
    """Удалить черновик карточки нового клиента, который так и не запустили.

    Args:
        client_name: имя или часть имени из карточки, например «Хилтон».

    Returns:
        Сколько черновиков удалено.
    """
    found = actions.find_drafts(client_name)
    for d in found:
        actions.drop_draft(d["id"])
    if not found:
        return f"Черновиков с «{client_name}» нет — возможно, уже удалён или запущен."
    return f"Удалено черновиков: {len(found)}. Кнопка «Запустить» под старой карточкой больше не сработает."


TOOLS = [list_clients, client_overview, propose_client, propose_search, manage_client, discard_draft]
