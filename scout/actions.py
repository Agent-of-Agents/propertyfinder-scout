"""Действия Scout Dubai — то, что делают и инструменты агента, и кнопки бота.

Все функции синхронные и блокирующие (Google, Property Finder — сеть);
bot.py вызывает их через asyncio.to_thread. Ничего не отправляют в Telegram:
возвращают данные или кладут карточки в scout.outbox.

Правила из HANDOFF §2, зашитые здесь:
- в колонки Алексея («✅ Одобрено», «📩 Запросить», «Моя цена», «Статус») пишем
  только из действий, которые вызвал он сам (кнопка, ответ на вопрос) — параметр
  allow_owner=True стоит только там;
- цена в презентацию — только та, что назвал Алексей;
- подбор не редактируется — закрывается и открывается новый;
- строки и листы не удаляются.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import uuid
from pathlib import Path

from lib import listing_detail, sheets, whatsapp
from lib.google_auth import CredentialsError

from . import books, cards, enrich, outbox, runner
from .models import (
    CLIENT_ACTIVE,
    CLIENT_ARCHIVED,
    CLOSE_REPLACED,
    MARKET_OFFPLAN,
    MARKET_SECONDARY,
    SEARCH_ACTIVE,
    SEARCH_CLOSED,
    SEARCH_PAUSED,
    Client,
    Search,
    slugify,
)
from .store import CLIENTS, DRAFTS, PENDING, RAW, SEARCHES, STATES, TG_MAP, Store, get_store

log = logging.getLogger("scout.actions")

AED_TO_USD = 3.6725
NEW_DAYS = 3


class NotConfigured(RuntimeError):
    """Google не настроен — действие с книгами невозможно."""


class NotFound(LookupError):
    """Клиент, подбор или объект не найден."""


# ------------------------------------------------------------------ окружение

def google_configured() -> bool:
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return bool(os.environ.get("DRIVE_FOLDER_ID"))
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    return bool(path and Path(path).exists() and os.environ.get("DRIVE_FOLDER_ID"))


def require_google() -> None:
    if not google_configured():
        raise NotConfigured(
            "Google не настроен: нужны GOOGLE_SERVICE_ACCOUNT_JSON (или путь в "
            "GOOGLE_APPLICATION_CREDENTIALS) и DRIVE_FOLDER_ID — папка «Подборки» на Общем диске."
        )


# ------------------------------------------------------------------ клиенты и подборы

def list_clients(store: Store | None = None, status: str | None = None) -> list[Client]:
    store = store or get_store()
    docs = store.find(CLIENTS, **({"status": status} if status else {}))
    return [Client.from_doc(d) for d in docs]


def get_client(slug: str, store: Store | None = None) -> Client:
    store = store or get_store()
    doc = store.get(CLIENTS, slug)
    if not doc:
        raise NotFound(f"Клиент «{slug}» не найден")
    return Client.from_doc(doc)


def find_client(hint: str, store: Store | None = None) -> Client | None:
    """По slug, фамилии или части имени; None, если не нашли или нашли двоих."""
    hint = (hint or "").strip().lower()
    if not hint:
        return None
    found = [c for c in list_clients(store)
             if c.slug == hint or hint in c.name.lower() or slugify(hint) == c.slug]
    return found[0] if len(found) == 1 else None


def client_by_topic(topic_id: int, store: Store | None = None) -> Client | None:
    for c in list_clients(store):
        if c.telegram_topic_id == topic_id:
            return c
    return None


def client_searches(client: Client | str, active_only: bool = False, store: Store | None = None) -> list[Search]:
    store = store or get_store()
    slug = client.slug if isinstance(client, Client) else client
    found = [Search.from_doc(d) for d in store.find(SEARCHES, client=slug)]
    if active_only:
        found = [s for s in found if s.status == SEARCH_ACTIVE]
    return sorted(found, key=lambda s: s.created)


def get_search(client_slug: str, search_slug: str, store: Store | None = None) -> Search:
    store = store or get_store()
    doc = store.get(SEARCHES, f"{client_slug}/{search_slug}")
    if not doc:
        raise NotFound(f"Подбор «{search_slug}» у клиента «{client_slug}» не найден")
    return Search.from_doc(doc)


def save_client(client: Client, store: Store | None = None) -> None:
    (store or get_store()).put(CLIENTS, client.slug, client.to_doc())


def save_search(search: Search, store: Store | None = None) -> None:
    (store or get_store()).put(SEARCHES, search.key, search.to_doc())


def set_topic(client: Client, topic_id: int, store: Store | None = None) -> None:
    client.telegram_topic_id = topic_id
    save_client(client, store)


def unique_slug(base: str, taken: set[str]) -> str:
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


# ------------------------------------------------------------------ черновики брифов

def save_draft(kind: str, payload: dict, store: Store | None = None) -> dict:
    """Черновик до кнопки «Запустить» / «Заменить» / «Добавить». kind: client | search."""
    store = store or get_store()
    draft = {"id": uuid.uuid4().hex[:10], "kind": kind, "created": dt.datetime.now().isoformat(), **payload}
    store.put(DRAFTS, draft["id"], draft)
    return draft


def get_draft(draft_id: str, store: Store | None = None) -> dict:
    doc = (store or get_store()).get(DRAFTS, draft_id)
    if not doc:
        raise NotFound("Черновик не найден — возможно, уже запущен или отменён")
    return doc


def drop_draft(draft_id: str, store: Store | None = None) -> None:
    (store or get_store()).delete(DRAFTS, draft_id)


def search_from_brief(client_slug: str, brief: dict, taken: set[str]) -> Search:
    """Search из полей брифа (что распознал агент)."""
    market = brief.get("market") or MARKET_SECONDARY
    title = brief.get("title") or _default_title(brief)
    search = Search(
        client=client_slug,
        slug=unique_slug(slugify(title), taken),
        title=title,
        market=market,
        sources=list(brief.get("sources") or []),
        bedrooms=[str(b) for b in brief.get("bedrooms") or []],
        budget=dict(brief.get("budget") or {}),
        offplan=dict(brief.get("offplan") or {}),
        wishes=list(brief.get("wishes") or []),
        stop=list(brief.get("stop") or []),
        distress_keywords=list(brief.get("distress_keywords") or []),
        target=brief.get("target") or "",
        handover=brief.get("handover") or "",
    )
    if search.market == MARKET_OFFPLAN and not search.offplan:
        search.offplan = {
            "bedrooms": search.bedrooms,
            "budget": {"max": search.budget.get("max")} if search.budget.get("max") else {},
            "communities": [search.target] if search.target else [],
            "sheet": search.title,
        }
    if not search.distress_keywords and search.target:
        search.distress_keywords = [search.target.lower()]
    return search


def _default_title(brief: dict) -> str:
    beds = "/".join(f"{b}BR" for b in brief.get("bedrooms") or [])
    prefix = "Off-plan " if brief.get("market") == MARKET_OFFPLAN else ""
    return " ".join(x for x in (prefix + (brief.get("target") or "Подбор"), beds) if x).strip()


# ------------------------------------------------------------------ создание

def create_client(brief_client: dict, store: Store | None = None) -> Client:
    """Клиент: карточка в хранилище, книга и папка на Диске. Тему создаёт bot.py."""
    store = store or get_store()
    require_google()
    name = (brief_client.get("name") or "").strip()
    if not name:
        raise ValueError("У клиента нет имени")
    taken = set(store.ids(CLIENTS))
    client = Client(
        slug=unique_slug(slugify(name.split()[0]), taken),
        name=name,
        contact=brief_client.get("contact") or "",
        deadline=brief_client.get("deadline") or "",
        notes=brief_client.get("notes") or "",
    )
    client.spreadsheet_id = books.create_client_book(client)
    save_client(client, store)
    return client


def create_search(client: Client, brief: dict, store: Store | None = None,
                  run_now: bool = True) -> tuple[Search, dict]:
    """Подбор: лист в книге, карточка в хранилище, первый сбор."""
    store = store or get_store()
    require_google()
    store.update(CLIENTS, client.slug, {"last_touch": dt.date.today().isoformat()})
    taken = {s.slug for s in client_searches(client, store=store)}
    search = search_from_brief(client.slug, brief, taken)
    books.create_search_sheet(client, search)
    save_search(search, store)
    if client.status != CLIENT_ACTIVE:                 # «Добавить параллельно» у архивного — он снова в работе
        client.status = CLIENT_ACTIVE
        save_client(client, store)
    report: dict = {}
    if run_now:
        report = runner.run_search(store, client, search)
        distress_found = []
        try:
            distress_found = runner.run_distress(client, [search], dt.date.today())
        except Exception as error:  # noqa: BLE001
            log.warning("Дистресс при создании %s: %s", search.key, error)
        rows = (store.get(RAW, search.key) or {}).get("rows", [])
        if search.market == MARKET_SECONDARY:
            report = {**report, **runner.first_collection_report(search, rows, len(distress_found))}
        else:
            report = {**report, "total": len(rows), "distress": len(distress_found)}
        _refresh_summary(client, store)
    return search, report


def close_search(client: Client, search: Search, reason: str, store: Store | None = None) -> Search:
    store = store or get_store()
    search.status = SEARCH_CLOSED
    search.close_reason = reason
    search.closed = dt.date.today().isoformat()
    save_search(search, store)
    if client.spreadsheet_id:
        try:
            books.close_search_sheet(client, search, reason)
        except Exception as error:  # noqa: BLE001
            log.warning("Лист %s не переименован: %s", search.key, error)
    _refresh_summary(client, store)
    if not client_searches(client, active_only=True, store=store):
        client.status = CLIENT_ARCHIVED
        save_client(client, store)
    return search


def pause_client(client: Client, store: Store | None = None) -> int:
    """⏸ Заморозить: все активные подборы на паузу, прогон по ним не идёт, книга и тема остаются."""
    store = store or get_store()
    n = 0
    for s in client_searches(client, active_only=True, store=store):
        set_search_status(client, s, SEARCH_PAUSED, store)
        n += 1
    return n


def resume_client(client: Client, store: Store | None = None) -> int:
    store = store or get_store()
    n = 0
    for s in client_searches(client, store=store):
        if s.status == SEARCH_PAUSED:
            set_search_status(client, s, SEARCH_ACTIVE, store)
            n += 1
    return n


def close_client(client: Client, reason: str, store: Store | None = None) -> list[Search]:
    """✓ Куплено / ✕ В архив: закрыть все живые подборы с причиной; клиент уходит в архив."""
    store = store or get_store()
    closed = []
    for s in client_searches(client, store=store):
        if s.status in (SEARCH_ACTIVE, SEARCH_PAUSED):
            closed.append(close_search(client, s, reason, store))
    client.status = CLIENT_ARCHIVED
    save_client(client, store)
    return closed


def delete_client(client: Client, store: Store | None = None) -> dict:
    """🗑 Удалить полностью: записи из хранилища, книга — в корзину Диска (30 дней на «передумал»).

    Единственное место, где Scout что-то удаляет, — только по кнопке с подтверждением.
    Тему в Telegram удаляет bot.py (нужен Bot API). Папка объектов на Диске остаётся:
    там могут лежать планировки от брокеров.
    """
    store = store or get_store()
    result = {"client": client.slug, "searches": 0, "book_trashed": False, "topic_id": client.telegram_topic_id}
    for s in client_searches(client, store=store):
        for coll in (STATES, RAW):
            store.delete(coll, s.key)
        store.delete(SEARCHES, s.key)
        result["searches"] += 1
    if client.spreadsheet_id:
        try:
            from lib import drive
            drive.trash_file(client.spreadsheet_id)
            result["book_trashed"] = True
        except Exception as error:  # noqa: BLE001
            log.warning("Книга %s в корзину не ушла: %s", client.slug, error)
    store.delete(CLIENTS, client.slug)
    return result


def find_drafts(hint: str, store: Store | None = None) -> list[dict]:
    """Черновики карточек по имени клиента в них."""
    store = store or get_store()
    hint = (hint or "").strip().lower()
    found = []
    for d in store.find(DRAFTS):
        name = ((d.get("client") or {}).get("name") or "").lower()
        if not hint or hint in name or (d.get("client_slug") and hint in d["client_slug"]):
            found.append(d)
    return found


def replace_search(client: Client, old: Search, brief: dict, store: Store | None = None) -> tuple[Search, dict]:
    close_search(client, old, CLOSE_REPLACED, store)
    return create_search(client, brief, store)


def set_search_status(client: Client, search: Search, status: str, store: Store | None = None) -> Search:
    if status not in (SEARCH_ACTIVE, SEARCH_PAUSED):
        raise ValueError(status)
    search.status = status
    save_search(search, store)
    if status == SEARCH_ACTIVE and client.status != CLIENT_ACTIVE:
        client.status = CLIENT_ACTIVE
        save_client(client, store)
    _refresh_summary(client, store)
    return search


def _refresh_summary(client: Client, store: Store) -> None:
    if not client.spreadsheet_id:
        return
    try:
        searches = client_searches(client, store=store)
        stats = {}
        for s in searches:
            if s.status == SEARCH_ACTIVE:
                st = books.search_stats(client, s)
                last = (store.get(STATES, s.key) or {}).get("last_report", {}).get("run_at", "")
                st["last_run"] = last
                stats[s.slug] = st
        books.update_summary(client, searches, stats)
    except Exception as error:  # noqa: BLE001
        log.warning("Сводка %s: %s", client.slug, error)


# ------------------------------------------------------------------ объекты

class SheetRows:
    """Лист подбора целиком: шапка, строки, запись по названию колонки с охраной."""

    def __init__(self, client: Client, search: Search):
        self.spreadsheet_id = client.spreadsheet_id
        self.title = search.title
        rows = sheets.read_range(self.spreadsheet_id, f"'{self.title}'!A1:AZ2000")
        if not rows:
            raise NotFound(f"Лист «{self.title}» пуст или не найден")
        self.header: list[str] = rows[0]
        self.rows: list[list] = rows[1:]

    def col(self, name: str) -> int:
        if name not in self.header:
            raise NotFound(f"В шапке листа «{self.title}» нет колонки «{name}»")
        return self.header.index(name)

    def cell(self, row: list, name: str) -> str:
        i = self.header.index(name) if name in self.header else -1
        return str(row[i]).strip() if 0 <= i < len(row) and row[i] is not None else ""

    def row_number(self, listing_id: str) -> int:
        for n, row in enumerate(self.rows, start=2):
            if row and str(row[0]).strip() == listing_id:
                return n
        raise NotFound(f"Объект {listing_id} не найден на листе «{self.title}»")

    def as_dict(self, row: list) -> dict:
        return {name: self.cell(row, name) for name in self.header}

    def write(self, listing_id: str, values: dict[str, object], allow_owner: bool = False) -> None:
        """Записать значения в строку объекта. Колонки Алексея — только с allow_owner."""
        n = self.row_number(listing_id)
        updates = {}
        for name, value in values.items():
            if name in books.OWNER_COLUMNS and not allow_owner:
                raise PermissionError(f"«{name}» — колонка Алексея, система в неё не пишет")
            updates[f"'{self.title}'!{_a1(self.col(name))}{n}"] = [[value]]
        sheets.update_ranges(self.spreadsheet_id, updates)


def _a1(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def raw_rows(search: Search, store: Store | None = None) -> list[dict]:
    doc = (store or get_store()).get(RAW, search.key) or {}
    return list(doc.get("rows", []))


def raw_row(search: Search, listing_id: str, store: Store | None = None) -> dict:
    for row in raw_rows(search, store):
        if row.get("id") == listing_id:
            return row
    raise NotFound(f"Объект {listing_id} нет в последней выгрузке подбора «{search.title}»")


def fresh_row(search: Search, listing_id: str, store: Store | None = None) -> dict:
    """Строка выгрузки с полями со страницы списка (фото, id брокера); нет — пересобрать список.

    Подборы, собранные до 18.09.2026, хранят строки без images/agent_id. Страница списка
    с сервера открыта, пересбор — секунды; сверку листа при этом не делаем.
    """
    store = store or get_store()
    row = raw_row(search, listing_id, store)
    if row.get("images") and row.get("pf_listing_id"):
        return row
    if search.market != MARKET_SECONDARY or not search.sources:
        return row
    log.info("Выгрузка %s без новых полей — пересобираю список", search.key)
    rows = runner.collect_listings(search)
    if rows:
        store.put(RAW, search.key, {"rows": rows, "collected": dt.date.today().isoformat()})
    for r in rows:
        if r.get("id") == listing_id:
            return r
    return row


def enrich_for_card(search: Search, row: dict, store: Store | None = None, with_photo: bool = True) -> dict:
    """Дельта к медиане, этаж из заголовка, первое фото — для карточки."""
    store = store or get_store()
    state = store.get(STATES, search.key) or {}
    med = (state.get("last_report") or {}).get("medians") or runner.medians(raw_rows(search, store))
    out = dict(row)
    out["market_delta"] = runner.market_delta(row, med)
    out.update(enrich.card_extras(row, raw_rows(search, store)))     # серия, дубли
    title = (row.get("title") or "").lower()
    for word, label in (("high floor", "высокий этаж"), ("mid floor", "средний этаж"), ("low floor", "низкий этаж")):
        if word in title:
            out["floor_hint"] = label
    if with_photo and search.market == MARKET_SECONDARY:
        images = row.get("images") or []
        if images:
            out["photo_url"] = images[0]                 # со страницы списка, карточка PF не нужна
        elif row.get("url"):
            try:
                detail = listing_detail.fetch_detail(row["url"])
                if detail.get("images"):
                    out["photo_url"] = detail["images"][0]
            except Exception as error:  # noqa: BLE001 — карточка без фото лучше, чем без карточки
                log.info("Фото для %s не получено: %s", row.get("id"), error)
    return out


def pending_cards(client: Client, search: Search, store: Store | None = None, limit: int = 30) -> list[dict]:
    """Новые объекты подбора, ещё не показанные и не пропущенные. 🇷🇺 сверху, дальше по цене к рынку."""
    store = store or get_store()
    state = store.get(STATES, search.key) or {}
    listings = state.get("listings", {})
    skipped = set(state.get("skipped", []))
    shown = set(state.get("shown", []))
    today = dt.date.today()
    rows = raw_rows(search, store)
    med = (state.get("last_report") or {}).get("medians") or runner.medians(rows)

    def is_new(row: dict) -> bool:
        seen = listings.get(row["id"], {})
        first = seen.get("first_seen")
        if not first:
            return True
        try:
            return (today - dt.date.fromisoformat(first)).days < NEW_DAYS
        except ValueError:
            return False

    fresh = [r for r in rows if r.get("id") not in skipped and r.get("id") not in shown and is_new(r)]
    if search.market == MARKET_SECONDARY:
        fresh = [r for r in fresh if runner.sync.fits_client(search.as_sync_client(client), r)]
        fresh.sort(key=lambda r: (-(r.get("ru_score") or 0), runner.market_delta(r, med) or 0))
    else:
        fresh.sort(key=lambda r: -(r.get("score") or 0))
    return fresh[:limit]


def mark_shown(search: Search, listing_ids: list[str], store: Store | None = None) -> None:
    store = store or get_store()
    state = store.get(STATES, search.key) or {}
    shown = set(state.get("shown", []))
    shown.update(listing_ids)
    store.update(STATES, search.key, {"shown": sorted(shown)})


def skip_listing(search: Search, listing_id: str, store: Store | None = None) -> None:
    store = store or get_store()
    state = store.get(STATES, search.key) or {}
    skipped = set(state.get("skipped", []))
    skipped.add(listing_id)
    store.update(STATES, search.key, {"skipped": sorted(skipped)})


def price_changes(search: Search, store: Store | None = None) -> list[dict]:
    state = (store or get_store()).get(STATES, search.key) or {}
    return (state.get("last_report") or {}).get("price", [])


def sheet_rows(client: Client, search: Search) -> list[dict]:
    """Все строки листа подбора словарями по названиям колонок — для вопросов Алексея."""
    view = SheetRows(client, search)
    return [view.as_dict(r) for r in view.rows if r and r[0]]


# ------------------------------------------------------------------ действия Алексея

def approve(client: Client, search: Search, listing_id: str, price: int | None,
            store: Store | None = None) -> dict:
    """✅ Одобрено (+ Моя цена) от имени Алексея; для secondary — сразу PDF."""
    store = store or get_store()
    view = SheetRows(client, search)
    values: dict[str, object] = {"✅ Одобрено": True}
    if price:
        values["Моя цена"] = int(price)
    view.write(listing_id, values, allow_owner=True)
    result = {"listing": listing_id, "price": price}
    if search.market == MARKET_SECONDARY and price:
        result.update(build_pdf(client, search, listing_id, int(price), store))
    return result


def build_pdf(client: Client, search: Search, listing_id: str, price: int,
              store: Store | None = None) -> dict:
    """Презентация по объекту — scripts/make_presentation поверх временной папки."""
    store = store or get_store()
    from scripts import make_presentation as mp

    work = runner.workdir(client)
    row = fresh_row(search, listing_id, store)          # при необходимости пересоберёт выгрузку
    runner.materialize_raw(store, search, work)
    mp.configure(work, {**search.as_sync_client(client), "raw": "raw/pf_listings.json"})
    sheet_row = {"id": listing_id, "type": f"{row.get('bedrooms')}BR",
                 "agency": row.get("agency") or "", "my_price": price}
    pdf, folder = mp.make(listing_id, price, sheet_row)
    link = mp.upload_to_drive(pdf, folder) if folder else None
    if link:
        SheetRows(client, search).write(listing_id, {"Презентация": link})
    return {
        "pdf_path": str(pdf),
        "link": link or "",
        "folder_link": (folder or {}).get("webViewLink", "") if folder else "",
        "usd": round(price / AED_TO_USD),
        "summary": f"{row.get('bedrooms')}BR · {row.get('size_m2')} м² · {row.get('agency') or ''}".strip(" ·"),
    }


def request_broker(client: Client, search: Search, listing_id: str, store: Store | None = None) -> dict:
    """📩 Запросить: свежая WhatsApp-ссылка PF, галочка от имени Алексея."""
    store = store or get_store()
    row = fresh_row(search, listing_id, store)
    if not row.get("url"):
        raise NotFound("У объекта нет ссылки на объявление")
    identity = whatsapp.identity_from_row(row) or whatsapp.listing_identity(row["url"])
    parts = whatsapp.request_link(identity, row["url"])
    if not whatsapp.looks_authentic(parts.get("text", "")):
        log.warning("Текст WhatsApp без attempt_id для %s", listing_id)
    SheetRows(client, search).write(listing_id, {"📩 Запросить": True}, allow_owner=True)
    row = dict(row)
    row["reference"] = identity.get("reference") or row.get("reference", "")
    return {"row": row, "link": parts["link"], "phone": parts.get("phone", "")}


def mark_sent(client: Client, search: Search, listing_id: str, store: Store | None = None) -> str:
    stamp = dt.date.today().strftime("%d.%m.%Y")
    SheetRows(client, search).write(listing_id, {"Запрошено": stamp, "📩 Запросить": False}, allow_owner=True)
    return stamp


def cancel_request(client: Client, search: Search, listing_id: str) -> None:
    SheetRows(client, search).write(listing_id, {"📩 Запросить": False}, allow_owner=True)


def attach_plan(client: Client, search: Search, listing_id: str, image: Path, as_photo: bool = False,
                store: Store | None = None) -> dict:
    """Планировка (или фото) от брокера → папка объекта → пересборка PDF, если есть цена."""
    store = store or get_store()
    from scripts import make_presentation as mp

    work = runner.workdir(client)
    sub = "photos_custom" if as_photo else "plans_custom"
    target = work / "approved" / listing_id / sub
    target.mkdir(parents=True, exist_ok=True)
    dest = target / f"tg_{dt.datetime.now():%Y%m%d_%H%M%S}{image.suffix.lower() or '.jpg'}"
    dest.write_bytes(image.read_bytes())

    view = SheetRows(client, search)
    n = view.row_number(listing_id)
    my_price = parse_int(view.cell(view.rows[n - 2], "Моя цена"))
    approved = view.cell(view.rows[n - 2], "✅ Одобрено").upper() == "TRUE"
    result = {"listing": listing_id, "saved": str(dest), "rebuilt": False}
    # На Диск — в папку объекта, откуда её же заберёт следующая сборка
    try:
        row = raw_row(search, listing_id, store)
        runner.materialize_raw(store, search, work)
        mp.configure(work, {**search.as_sync_client(client), "raw": "raw/pf_listings.json"})
        detail = {"size_m2": row.get("size_m2")}
        folder = mp.unit_folder({"type": f"{row.get('bedrooms')}BR", "agency": row.get("agency") or "",
                                 "my_price": my_price or row.get("price") or 0}, detail)
        _upload_image(dest, folder)
        result["folder_link"] = folder.get("webViewLink", "")
    except Exception as error:  # noqa: BLE001
        log.warning("Планировка на Диск не легла: %s", error)
    if approved and my_price and search.market == MARKET_SECONDARY:
        result.update(build_pdf(client, search, listing_id, my_price, store))
        result["rebuilt"] = True
        result["price"] = my_price
    return result


def _upload_image(path: Path, folder: dict) -> None:
    from googleapiclient.http import MediaFileUpload

    from lib.google_auth import drive_service

    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    drive_service().files().create(
        body={"name": path.name, "parents": [folder["id"]]},
        media_body=MediaFileUpload(str(path), mimetype=mime), fields="id", supportsAllDrives=True,
    ).execute()


def parse_int(value: str) -> int | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if digits else None


# ------------------------------------------------------------------ ожидания и карта кнопок

def set_pending(key: str, payload: dict, store: Store | None = None) -> None:
    (store or get_store()).put(PENDING, key, {**payload, "since": dt.datetime.now().isoformat()})


def pop_pending(key: str, store: Store | None = None) -> dict | None:
    store = store or get_store()
    doc = store.get(PENDING, key)
    if doc:
        store.delete(PENDING, key)
    return doc


def remember_message(message_key: str, address: dict, store: Store | None = None) -> None:
    """message_id карточки → адрес объекта: так работает reply фотографией."""
    (store or get_store()).put(TG_MAP, message_key, address)


def recall_message(message_key: str, store: Store | None = None) -> dict | None:
    return (store or get_store()).get(TG_MAP, message_key)


# ------------------------------------------------------------------ панель и списки

def clients_overview(store: Store | None = None) -> list[dict]:
    store = store or get_store()
    items = []
    for client in list_clients(store):
        searches = client_searches(client, store=store)
        active = [s for s in searches if s.status == SEARCH_ACTIVE]
        approved = 0
        for s in active:
            approved += ((store.get(STATES, s.key) or {}).get("stats") or {}).get("approved", 0)
        icon = active[0].icon if active else "•"
        items.append({"slug": client.slug, "name": client.name, "status": client.status,
                      "searches": len(active), "approved": approved, "deadline": client.deadline,
                      "icon": icon, "topic": client.telegram_topic_id})
    return items


def panel_update(store: Store | None = None, topic_link: callable = None) -> str:
    """Книга «Панель»: строка на каждый подбор всех клиентов."""
    store = store or get_store()
    require_google()
    panel_id = books.ensure_panel()
    rows = []
    for client in list_clients(store):
        for s in client_searches(client, store=store):
            stats = {}
            if s.status == SEARCH_ACTIVE and client.spreadsheet_id:
                try:
                    stats = books.search_stats(client, s)
                except Exception:  # noqa: BLE001
                    stats = {}
            stats["last_run"] = (store.get(STATES, s.key) or {}).get("last_report", {}).get("run_at", "")
            link = topic_link(client) if topic_link and client.telegram_topic_id else ""
            rows.append(books.panel_row(client, s, stats, link))
    rows.sort(key=lambda r: (r[3].startswith("✕") or r[3].startswith("✓"), r[9] or "9999"))
    books.write_panel(panel_id, rows)
    return books.sheet_url(panel_id)


# ------------------------------------------------------------------ дневной прогон

def daily(store: Store | None = None, only: str | None = None) -> dict:
    store = store or get_store()
    require_google()
    runner.restore_catalog(store)
    pairs = []
    for client in list_clients(store, status=CLIENT_ACTIVE):
        if only and client.slug != only:
            continue
        pairs.append((client, client_searches(client, store=store)))
    result = runner.daily_run(store, pairs)
    for slug, entry in result.items():
        if slug.startswith("_"):
            continue
        for s_slug, stats in (entry.get("stats") or {}).items():
            store.update(STATES, f"{slug}/{s_slug}", {"stats": stats})
    return result


def approved_without_pdf(client: Client, search: Search) -> list[dict]:
    """Галочки ✅ с ценой, у которых ещё нет презентации — их собирает прогон."""
    if search.market != MARKET_SECONDARY:
        return []
    view = SheetRows(client, search)
    found = []
    for row in view.rows:
        if not row or not row[0]:
            continue
        if view.cell(row, "✅ Одобрено").upper() != "TRUE" or view.cell(row, "Презентация"):
            continue
        price = parse_int(view.cell(row, "Моя цена"))
        found.append({"id": str(row[0]).strip(), "price": price})
    return found

