"""Структурированный ответ агента «propertyfinder-scout».

Подключается так:

    from schemas import AgentAnswer
    create_agent(..., response_format=AgentAnswer)
    result["structured_response"]  # -> AgentAnswer

По умолчанию агент отвечает свободным текстом; включай схему, когда ответ
потребляет код, а не человек.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentAnswer(BaseModel):
    """Ответ агента в машиночитаемом виде."""

    answer: str = Field(description="Ответ пользователю обычным текстом")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Уверенность в ответе от 0 до 1"
    )
