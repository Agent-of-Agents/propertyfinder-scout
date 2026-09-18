"""Модель «клиент → подбор → объект» (docs/HANDOFF.md §1).

Клиент — человек и сделка: одна книга Google, одна папка, одна тема в Telegram.
Подбор — один запрос с рынком, источниками и фильтрами: лист в книге клиента.
Объект — строка листа с якорем listing_id; отдельной сущности в хранилище нет,
он живёт в таблице и в состоянии сверки.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field

MARKET_SECONDARY = "secondary"
MARKET_OFFPLAN = "offplan"
MARKETS = (MARKET_SECONDARY, MARKET_OFFPLAN)

CLIENT_ACTIVE, CLIENT_ARCHIVED = "active", "archived"
SEARCH_ACTIVE, SEARCH_PAUSED, SEARCH_CLOSED = "active", "paused", "closed"
CLOSE_BOUGHT, CLOSE_REPLACED, CLOSE_DROPPED = "bought", "replaced", "dropped"

MARKET_ICON = {MARKET_SECONDARY: "🏠", MARKET_OFFPLAN: "🏗"}

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(text: str) -> str:
    """«Marina Shores 2BR» → marina-shores-2br; «Иванова Мария» → ivanova-mariya."""
    out = "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out or "x"


def today_iso() -> str:
    return dt.date.today().isoformat()


@dataclass
class Client:
    slug: str
    name: str
    contact: str = ""
    status: str = CLIENT_ACTIVE
    spreadsheet_id: str = ""
    drive_folder_id: str = ""
    telegram_topic_id: int | None = None
    created: str = field(default_factory=today_iso)
    deadline: str = ""            # дата сделки или приезда, ISO
    notes: str = ""
    last_touch: str = ""          # последнее действие Алексея по клиенту, ISO-дата (scout/reminders.py)
    followup_days: int = 7        # через сколько дней тишины напомнить коснуться
    followup_sent: str = ""       # когда последний раз напоминали

    def to_doc(self) -> dict:
        return asdict(self)

    @classmethod
    def from_doc(cls, doc: dict) -> "Client":
        known = {k: v for k, v in doc.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    @property
    def short_name(self) -> str:
        """Фамилия для тегов: «Иванова Мария» → «Иванова»."""
        return self.name.split()[0] if self.name else self.slug


@dataclass
class Search:
    client: str                   # slug клиента
    slug: str
    title: str                    # имя листа: «Marina Shores 2BR»
    market: str = MARKET_SECONDARY
    status: str = SEARCH_ACTIVE
    created: str = field(default_factory=today_iso)
    closed: str = ""
    close_reason: str = ""
    # secondary
    sources: list[dict] = field(default_factory=list)      # [{"kind": "propertyfinder", "url": …}]
    bedrooms: list[str] = field(default_factory=list)      # ["1", "2"]
    budget: dict = field(default_factory=dict)             # {"min": …, "max": …}
    # offplan — блок в формате lib/offplan (см. докстринг там)
    offplan: dict = field(default_factory=dict)
    # общее
    wishes: list[str] = field(default_factory=list)
    stop: list[str] = field(default_factory=list)
    distress_keywords: list[str] = field(default_factory=list)
    target: str = ""              # проект или район — для папки на Диске и заголовков
    handover: str = ""

    def to_doc(self) -> dict:
        return asdict(self)

    @classmethod
    def from_doc(cls, doc: dict) -> "Search":
        known = {k: v for k, v in doc.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    @property
    def key(self) -> str:
        return f"{self.client}/{self.slug}"

    @property
    def icon(self) -> str:
        return MARKET_ICON.get(self.market, "•")

    @property
    def sheet(self) -> str:
        return self.title

    def as_sync_client(self, client: Client) -> dict:
        """Словарь в форме, которую ждут lib.sync.run, lib.distress и make_presentation."""
        return {
            "name": client.name,
            "target": self.target or self.title,
            "spreadsheet_id": client.spreadsheet_id,
            "sheet": self.title,
            "sources": self.sources,
            "bedrooms": self.bedrooms,
            "budget": self.budget,
            "distress_keywords": self.distress_keywords,
            "handover": self.handover,
        }

    def as_offplan_brief(self) -> dict:
        brief = dict(self.offplan)
        brief.setdefault("sheet", self.title)
        return brief


def tag(client: Client, search: Search | None = None) -> str:
    """Первая строка любого сообщения об объекте: «Иванова · Marina Shores 2BR»."""
    return f"{client.short_name} · {search.title}" if search else client.short_name
