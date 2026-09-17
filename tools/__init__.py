"""Реестр инструментов агента «Scout Dubai».

create_agent получает именно список TOOLS. Новый инструмент — новый модуль
в этом пакете плюс строка в TOOLS.

catalog.py     — разведка страниц propertyfinder.ae (стандарт фабрики)
scout_clients  — клиенты и подборы: список, обзор, карточки брифов на подтверждение
scout_objects  — строки листов для ответов на вопросы, карточки объектов, проверка URL PF
scout_ops      — прогон сейчас, Панель, каталог проектов
"""

from __future__ import annotations

from .catalog import extract_listings, open_catalog_page
from .scout_clients import TOOLS as CLIENT_TOOLS
from .scout_objects import TOOLS as OBJECT_TOOLS
from .scout_ops import TOOLS as OPS_TOOLS

TOOLS = [open_catalog_page, extract_listings, *CLIENT_TOOLS, *OBJECT_TOOLS, *OPS_TOOLS]

__all__ = ["TOOLS", "open_catalog_page", "extract_listings"]
