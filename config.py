"""Настройки агента «propertyfinder-scout». Всё, что меняется между средами, — здесь."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODEL = "anthropic:claude-opus-5"


@dataclass(frozen=True)
class AgentConfig:
    """Конфигурация одного экземпляра агента."""

    model: str = DEFAULT_MODEL
    max_model_retries: int = 3
    max_tool_retries: int = 2

    # Память сессий. Пустой URI — агент работает без неё, диалог живёт
    # до конца запроса. Так он запускается локально и проходит тесты.
    mongodb_uri: str = ""
    mongodb_db: str = "propertyfinder-scout"
    memory_ttl_seconds: int | None = None

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """Собрать конфиг из окружения (.env подхватывается, если есть python-dotenv)."""
        _load_dotenv()
        return cls(
            model=os.environ.get("AGENT_MODEL", DEFAULT_MODEL),
            max_model_retries=_int_env("AGENT_MAX_MODEL_RETRIES", 3),
            max_tool_retries=_int_env("AGENT_MAX_TOOL_RETRIES", 2),
            mongodb_uri=os.environ.get("MONGODB_URI", ""),
            mongodb_db=os.environ.get("MONGODB_DB", "propertyfinder-scout"),
            memory_ttl_seconds=_optional_int_env("AGENT_MEMORY_TTL_SECONDS"),
        )

    def require_api_key(self) -> None:
        """Проверить наличие ключа провайдера до первого запроса к модели."""
        key = "ANTHROPIC_API_KEY"
        if key and not os.environ.get(key):
            raise RuntimeError(
                f"Не задана переменная окружения {key}. "
                "Скопируй .env.example в .env и заполни её."
            )


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _optional_int_env(name: str) -> int | None:
    """Как _int_env, но «не задано» — это None, а не значение по умолчанию."""
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()
