"""Смоук-тесты агента «propertyfinder-scout». Сеть не трогают."""

from __future__ import annotations

import agent
from config import AgentConfig
from tools import TOOLS


def test_config_defaults_to_blueprint_model() -> None:
    assert AgentConfig().model == "anthropic:claude-opus-5"


def test_tools_are_registered() -> None:
    assert TOOLS, "список TOOLS пуст — агенту нечем работать"
    for item in TOOLS:
        assert item.name, "у инструмента нет имени"
        assert item.description, f"у инструмента {item.name} нет описания (docstring)"


def test_system_prompt_is_not_empty() -> None:
    assert agent.SYSTEM_PROMPT.strip()


def test_agent_builds() -> None:
    built = agent.build_agent(AgentConfig())
    assert built is not None


def test_foreign_host_is_rejected() -> None:
    """Инструмент не должен ходить куда угодно — только на propertyfinder.ae."""
    from tools.catalog import open_catalog_page

    answer = open_catalog_page.invoke({"path": "https://example.com/page"})
    assert "Отказано" in answer


def test_robots_disallowed_path_is_rejected() -> None:
    """Разделы, закрытые в robots.txt сайта, отсекаются до запроса."""
    from tools.catalog import extract_listings

    answer = extract_listings.invoke({"path": "/en/search?c=1"})
    assert "robots.txt" in answer


def test_foreign_host_is_rejected() -> None:
    """Инструмент не должен ходить куда угодно — только на propertyfinder.ae."""
    from tools.catalog import open_catalog_page

    answer = open_catalog_page.invoke({"path": "https://example.com/page"})
    assert "Отказано" in answer


def test_robots_disallowed_path_is_rejected() -> None:
    from tools.catalog import extract_listings

    answer = extract_listings.invoke({"path": "/en/search?c=1"})
    assert "robots.txt" in answer
