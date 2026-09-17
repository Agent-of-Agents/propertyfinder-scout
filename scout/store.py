"""Хранилище состояния Scout Dubai.

У контейнера нет тома: пересборка стирает файловую систему. Поэтому всё, что
должно пережить перезапуск — карточки клиентов, подборы, состояние сверки,
привязка кнопок к объектам — живёт в MongoDB (MONGODB_URI уже в стандарте
фабрики). Без MongoDB хранилище работает на JSON-файлах в DATA_DIR — так
запускаются локальные прогоны и тесты.

Интерфейс нарочно узкий: коллекция → документ по id. Никаких запросов сложнее
фильтра по равенству — их и не нужно.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable

# Имена коллекций — с префиксом, чтобы не пересекаться с памятью диалогов
# (langgraph-checkpoint-mongodb живёт в той же базе).
CLIENTS = "scout_clients"
SEARCHES = "scout_searches"
STATES = "scout_states"          # состояние сверки по подбору
RAW = "scout_raw"                # последняя выгрузка объявлений по подбору
TG_MAP = "scout_tg_map"          # callback_data / message_id → адрес объекта
PENDING = "scout_pending"        # ожидание ответа Алексея (цена, подтверждение)
DRAFTS = "scout_drafts"          # черновики брифов до кнопки «Запустить»
META = "scout_meta"              # панель, отметки прогонов, прочее по одному документу


class Store:
    """Документы по коллекциям. Бэкенд выбирается по окружению."""

    def __init__(self, mongodb_uri: str = "", mongodb_db: str = "", data_dir: str | Path = ""):
        self._lock = threading.RLock()
        self._mongo = None
        if mongodb_uri:
            from pymongo import MongoClient

            client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=10_000, appname="scout-dubai")
            self._mongo = client[mongodb_db or "propertyfinder-scout"]
            self.backend = "mongodb"
        else:
            self._dir = Path(data_dir or os.environ.get("DATA_DIR", "data/scout"))
            self.backend = "files"

    # ------------------------------------------------------------ базовые
    def get(self, collection: str, doc_id: str) -> dict | None:
        with self._lock:
            if self._mongo is not None:
                doc = self._mongo[collection].find_one({"_id": doc_id})
                if doc is None:
                    return None
                doc = dict(doc)
                doc.pop("_id", None)
                return doc
            path = self._path(collection, doc_id)
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))

    def put(self, collection: str, doc_id: str, doc: dict) -> None:
        with self._lock:
            body = dict(doc)
            if self._mongo is not None:
                body["_id"] = doc_id
                self._mongo[collection].replace_one({"_id": doc_id}, body, upsert=True)
                return
            path = self._path(collection, doc_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")

    def update(self, collection: str, doc_id: str, fields: dict) -> dict:
        """Обновить часть полей; документа нет — создать."""
        with self._lock:
            doc = self.get(collection, doc_id) or {}
            doc.update(fields)
            self.put(collection, doc_id, doc)
            return doc

    def delete(self, collection: str, doc_id: str) -> None:
        with self._lock:
            if self._mongo is not None:
                self._mongo[collection].delete_one({"_id": doc_id})
                return
            path = self._path(collection, doc_id)
            if path.exists():
                path.unlink()

    def find(self, collection: str, **where: Any) -> list[dict]:
        """Все документы коллекции, у которых поля равны заданным. Порядок — по id."""
        with self._lock:
            if self._mongo is not None:
                found = []
                for doc in self._mongo[collection].find(where or {}).sort("_id", 1):
                    doc = dict(doc)
                    doc["id"] = doc.pop("_id")
                    found.append(doc)
                return found
            folder = self._dir / collection
            if not folder.exists():
                return []
            found = []
            for path in sorted(folder.glob("*.json")):
                doc = json.loads(path.read_text(encoding="utf-8"))
                if all(doc.get(k) == v for k, v in where.items()):
                    doc["id"] = path.stem
                    found.append(doc)
            return found

    def ids(self, collection: str) -> Iterable[str]:
        return [d["id"] for d in self.find(collection)]

    # ------------------------------------------------------------ состояние сверки
    def state_handle(self, key: str) -> "StateHandle":
        """Объект с load()/save() — его принимает lib.sync.run вместо пути к файлу."""
        return StateHandle(self, key)

    # ------------------------------------------------------------ служебное
    def _path(self, collection: str, doc_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_.:" else "_" for ch in doc_id)
        return self._dir / collection / f"{safe}.json"


class StateHandle:
    """Состояние сверки одного подбора, совместимое с lib.sync.load_state/save_state."""

    def __init__(self, store: Store, key: str):
        self.store = store
        self.key = key

    def load(self) -> dict:
        return self.store.get(STATES, self.key) or {"listings": {}}

    def save(self, state: dict) -> None:
        self.store.put(STATES, self.key, state)


_default: Store | None = None


def get_store() -> Store:
    """Хранилище по окружению: MONGODB_URI → MongoDB, иначе файлы в DATA_DIR."""
    global _default
    if _default is None:
        _default = Store(
            mongodb_uri=os.environ.get("MONGODB_URI", ""),
            mongodb_db=os.environ.get("MONGODB_DB", "propertyfinder-scout"),
        )
    return _default


def set_store(store: Store | None) -> None:
    """Подменить хранилище — для тестов."""
    global _default
    _default = store
