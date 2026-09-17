"""Исходящие сообщения, которые инструменты просят бота отправить.

Инструмент LangChain возвращает модели текст, а карточку с кнопками должен
получить Telegram. Инструмент кладёт Outgoing в исходящий ящик текущего
запроса; bot.py после ответа агента забирает ящик и отправляет всё в ту тему,
откуда пришёл запрос. Вне бота (CLI, тесты) ящик просто накапливается.
"""

from __future__ import annotations

from contextvars import ContextVar

from .cards import Outgoing

_outbox: ContextVar[list[Outgoing] | None] = ContextVar("scout_outbox", default=None)


def begin() -> None:
    _outbox.set([])


def push(message: Outgoing) -> None:
    box = _outbox.get()
    if box is None:
        box = []
        _outbox.set(box)
    box.append(message)


def drain() -> list[Outgoing]:
    box = _outbox.get() or []
    _outbox.set([])
    return list(box)
