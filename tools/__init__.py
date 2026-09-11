"""Реестр инструментов агента «propertyfinder-scout».

create_agent получает именно список TOOLS. Новый инструмент — новый модуль
в этом пакете плюс строка в TOOLS.
"""

from __future__ import annotations

from .catalog import extract_listings, open_catalog_page

TOOLS = [open_catalog_page, extract_listings]

__all__ = ["TOOLS", "open_catalog_page", "extract_listings"]
