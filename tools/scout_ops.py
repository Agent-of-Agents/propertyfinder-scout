"""Инструменты агента: прогон, панель, каталог проектов."""

from __future__ import annotations

from langchain.tools import tool

from scout import actions, runner
from scout.store import get_store


@tool
def run_daily_now(client: str = "") -> str:
    """Запустить прогон сейчас, не дожидаясь 08:00: сбор, сверка, дистресс, сводки.

    Долго: минуты на клиента. Вызывай только по прямой просьбе Алексея.

    Args:
        client: slug клиента, чтобы прогнать одного; пусто — всех активных.

    Returns:
        Строки отчёта по каждому подбору.
    """
    only = None
    if client:
        c = actions.find_client(client)
        if c is None:
            return f"Клиент «{client}» не найден."
        only = c.slug
    result = actions.daily(only=only)
    lines = []
    for slug, entry in result.items():
        if slug.startswith("_"):
            lines.append(f"каталог: {entry.get('error')}")
            continue
        for title, event in entry.get("log", []):
            lines.append(f"{entry['client'].short_name} · {title}: {event}")
        for err in entry.get("errors", []):
            lines.append(f"{entry['client'].short_name} · ⚠️ {err}")
    return "\n".join(lines) or "Активных подборов нет."


@tool
def panel_link() -> str:
    """Обновить книгу «Панель» (все подборы всех клиентов) и вернуть ссылку на неё.

    Returns:
        Ссылка на Google-книгу «Панель — Scout Dubai».
    """
    return actions.panel_update()


@tool
def catalog_refresh() -> str:
    """Обновить каталог проектов застройщиков с Property Finder «New projects».

    Нужен подборам offplan. С нуля — около 45 минут, по кэшу — 5–10. Вызывай только по просьбе.

    Returns:
        Сколько проектов в каталоге.
    """
    return runner.refresh_catalog(get_store())


TOOLS = [run_daily_now, panel_link, catalog_refresh]
