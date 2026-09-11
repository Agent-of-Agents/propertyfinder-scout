"""Точка сборки агента «propertyfinder-scout».

Модуль импортируется без сайд-эффектов: агент создаётся только внутри
build_agent(). Так его можно импортировать в тестах и в рантайме деплоя,
не требуя API-ключа на момент импорта.
"""

from __future__ import annotations

from langchain.agents import create_agent

from config import AgentConfig
from middleware import build_middleware
from tools import TOOLS

SYSTEM_PROMPT = """\
Ты — разведчик каталога недвижимости propertyfinder.ae (ОАЭ). Открывай страницы каталога инструментами и описывай, что на них реально видно. Опирайся только на данные инструментов, ничего не додумывай. Отвечай по-русски, коротко и по делу.
"""


def build_agent(config: AgentConfig | None = None, checkpointer=None):
    """Собрать и вернуть агента «propertyfinder-scout».

    checkpointer передаётся снаружи, а не создаётся здесь: подключение к базе
    памяти — это сетевой вызов, а модуль должен собираться и в тестах, и при
    agent_import_check, где никакой базы нет. Готовый чекпоинтер даёт
    memory.build_checkpointer(); без него агент помнит только текущий запрос.
    """
    cfg = config or AgentConfig.from_env()
    return create_agent(
        model=cfg.model,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        middleware=build_middleware(cfg),
        checkpointer=checkpointer,
        name="propertyfinder-scout",
    )


def run(message: str, config: AgentConfig | None = None) -> str:
    """Однократный прогон агента: сообщение на вход, текст ответа на выход."""
    agent = build_agent(config)
    result = agent.invoke({"messages": [{"role": "user", "content": message}]})
    return result["messages"][-1].text
