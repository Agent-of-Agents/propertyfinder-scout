"""Middleware агента «propertyfinder-scout»: ретраи и защита необратимых действий."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from config import AgentConfig

# Инструменты, вызов которых требует подтверждения человека.
# Добавляй сюда всё, что пишет, отправляет, платит или удаляет.
INTERRUPT_ON: dict[str, bool] = {}


def build_middleware(config: "AgentConfig") -> list[Any]:
    """Собрать стек middleware под конкретный конфиг."""
    from langchain.agents.middleware import (
        ModelRetryMiddleware,
        ToolRetryMiddleware,
    )

    middleware: list[Any] = [
        ModelRetryMiddleware(max_retries=config.max_model_retries),
        ToolRetryMiddleware(max_retries=config.max_tool_retries),
    ]

    if INTERRUPT_ON:
        from langchain.agents.middleware import HumanInTheLoopMiddleware

        middleware.append(HumanInTheLoopMiddleware(interrupt_on=dict(INTERRUPT_ON)))

    return middleware


__all__ = ["build_middleware", "INTERRUPT_ON"]
