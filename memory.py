"""Память сессий агента «propertyfinder-scout» — чекпоинтер поверх MongoDB.

Без MongoDB агент тоже работает: build_checkpointer() вернёт None, и диалог
будет жить только внутри одного запроса. Это осознанный компромисс —
отсутствие базы не должно мешать запустить агента локально или прогнать тесты.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from config import AgentConfig


def build_checkpointer(config: "AgentConfig") -> Any | None:
    """Собрать чекпоинтер сессий. None — если MongoDB не настроена.

    Внимание: конструктор MongoDBSaver сразу идёт в базу создавать индексы.
    Поэтому вызывать это стоит на старте процесса, а не при импорте модуля.
    """
    if not config.mongodb_uri:
        return None

    from langgraph.checkpoint.mongodb import MongoDBSaver
    from pymongo import MongoClient

    client = MongoClient(
        config.mongodb_uri,
        serverSelectionTimeoutMS=10_000,
        appname="propertyfinder-scout",
    )
    return MongoDBSaver(
        client,
        db_name=config.mongodb_db,
        ttl=config.memory_ttl_seconds,
    )
