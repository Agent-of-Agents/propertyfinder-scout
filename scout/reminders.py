"""Напоминания и фоллоуапы (решение Алексея 18.09.2026).

Ручные: «напомни в четверг предложить Гарееву Palm Villas» → агент разбирает дату,
запись в хранилище, в срок — карточка в тему клиента с кнопками
✔ Сделано · ⏰ +3 дня · ⏰ +7 дней. Время по Дубаю; в тихие часы переносится на утро.

Автоматические: клиент активен, но N дней (client.followup_days, по умолчанию 7)
по нему нет ни одного действия Алексея — ни ✅, ни 📩, ни сообщения в теме.
Тогда утром в тему приходит «⏳ молчит N дней» с тем, что накопилось, и кнопками.
Повторно — не раньше чем через те же N дней или после нового касания.

Всё в MongoDB (scout_reminders + поля клиента), переживает перезапуск контейнера.
"""

from __future__ import annotations

import datetime as dt
import uuid

from .models import CLIENT_ACTIVE, Client
from .store import CLIENTS, Store, get_store

REMINDERS = "scout_reminders"
DEFAULT_FOLLOWUP_DAYS = 7
MORNING = dt.time(9, 0)


def _now(tz) -> dt.datetime:
    return dt.datetime.now(tz)


# ------------------------------------------------------------------ ручные напоминания

def add(client_slug: str, due: dt.datetime, text: str, store: Store | None = None,
        source: str = "manual") -> dict:
    store = store or get_store()
    rem = {
        "id": uuid.uuid4().hex[:10],
        "client": client_slug,
        "text": text.strip(),
        "due": due.isoformat(),
        "created": dt.datetime.now(due.tzinfo).isoformat(),
        "status": "pending",
        "source": source,
    }
    store.put(REMINDERS, rem["id"], rem)
    return rem


def get(rem_id: str, store: Store | None = None) -> dict | None:
    doc = (store or get_store()).get(REMINDERS, rem_id)
    if doc:
        doc["id"] = rem_id
    return doc


def pending(client_slug: str | None = None, store: Store | None = None) -> list[dict]:
    store = store or get_store()
    items = store.find(REMINDERS, status="pending")
    if client_slug:
        items = [r for r in items if r.get("client") == client_slug]
    return sorted(items, key=lambda r: r.get("due", ""))


def due_now(now: dt.datetime, store: Store | None = None) -> list[dict]:
    """Напоминания, чей срок наступил. now — aware datetime в TZ бота."""
    out = []
    for r in pending(store=store):
        try:
            due = dt.datetime.fromisoformat(r["due"])
        except ValueError:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=now.tzinfo)
        if due <= now:
            out.append(r)
    return out


def complete(rem_id: str, store: Store | None = None) -> None:
    (store or get_store()).update(REMINDERS, rem_id, {"status": "done", "done": dt.datetime.now().isoformat()})


def cancel(rem_id: str, store: Store | None = None) -> None:
    (store or get_store()).update(REMINDERS, rem_id, {"status": "cancelled"})


def snooze(rem_id: str, days: int, tz, store: Store | None = None) -> dict:
    """Перенести на N дней вперёд, на утро."""
    store = store or get_store()
    due = dt.datetime.combine(_now(tz).date() + dt.timedelta(days=days), MORNING, tzinfo=tz)
    return store.update(REMINDERS, rem_id, {"due": due.isoformat(), "status": "pending", "snoozed": days})


def defer_to_morning(due: dt.datetime, quiet: tuple[dt.time, dt.time] | None) -> dt.datetime:
    """Срок попал в тихие часы — сдвинуть на утро."""
    if not quiet:
        return due
    start, end = quiet
    t = due.time()
    in_quiet = (t >= start or t < end) if start > end else (start <= t < end)
    if not in_quiet:
        return due
    day = due.date() + (dt.timedelta(days=1) if t >= start and start > end else dt.timedelta(0))
    return dt.datetime.combine(day, end, tzinfo=due.tzinfo)


def parse_when(text: str, tz) -> dt.datetime | None:
    """ISO-дата/время от агента: «2026-09-25», «2026-09-25T10:00». Без времени — 10:00."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return dt.datetime.combine(dt.date.fromisoformat(text), dt.time(10, 0), tzinfo=tz)
        value = dt.datetime.fromisoformat(text)
        return value if value.tzinfo else value.replace(tzinfo=tz)
    except ValueError:
        return None


def describe_due(due_iso: str, tz) -> str:
    try:
        due = dt.datetime.fromisoformat(due_iso)
    except ValueError:
        return due_iso
    if due.tzinfo is None:
        due = due.replace(tzinfo=tz)
    due = due.astimezone(tz)
    days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][due.weekday()]
    return f"{days} {due:%d.%m} в {due:%H:%M}"


# ------------------------------------------------------------------ касания и автофоллоуапы

def touch(client: Client | str, store: Store | None = None) -> None:
    """Любое действие Алексея по клиенту: ✅, 📩, сообщение в теме, планировка, новый подбор."""
    store = store or get_store()
    slug = client.slug if isinstance(client, Client) else client
    store.update(CLIENTS, slug, {"last_touch": dt.date.today().isoformat()})


def quiet_clients(clients: list[Client], today: dt.date) -> list[tuple[Client, int]]:
    """Активные клиенты без касаний дольше своего порога и без недавнего автофоллоуапа."""
    out = []
    for c in clients:
        if c.status != CLIENT_ACTIVE:
            continue
        threshold = int(getattr(c, "followup_days", DEFAULT_FOLLOWUP_DAYS) or DEFAULT_FOLLOWUP_DAYS)
        last = getattr(c, "last_touch", "") or c.created
        try:
            silent = (today - dt.date.fromisoformat(last)).days
        except ValueError:
            continue
        if silent < threshold:
            continue
        sent = getattr(c, "followup_sent", "") or ""
        try:
            since_sent = (today - dt.date.fromisoformat(sent)).days if sent else threshold
        except ValueError:
            since_sent = threshold
        if since_sent < threshold:
            continue
        out.append((c, silent))
    return out


def mark_followup_sent(client: Client, today: dt.date, store: Store | None = None) -> None:
    (store or get_store()).update(CLIENTS, client.slug, {"followup_sent": today.isoformat()})
