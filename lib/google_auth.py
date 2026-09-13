"""Единая точка авторизации в Google API через сервисный аккаунт.

Ключ никогда не хранится в коде. Два способа его передать:
  - GOOGLE_APPLICATION_CREDENTIALS — путь к JSON-файлу (локально: secrets/, закрыт .gitignore);
  - GOOGLE_SERVICE_ACCOUNT_JSON   — содержимое JSON целиком (контейнер: секрет в .env,
    тома и файлов у контейнера нет). Если задана — используется она.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class CredentialsError(RuntimeError):
    """Ключ сервисного аккаунта не найден или задан неверно."""


def credentials_path() -> Path:
    """Абсолютный путь к JSON-ключу. Относительный путь в .env считается от корня проекта."""
    raw = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not raw:
        raise CredentialsError(
            "Не задана переменная GOOGLE_APPLICATION_CREDENTIALS. "
            "Скопируй .env.example в .env и укажи путь к JSON-ключу."
        )

    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path

    if not path.exists():
        raise CredentialsError(f"JSON-ключ сервисного аккаунта не найден: {path}")

    return path


@lru_cache(maxsize=1)
def get_credentials() -> service_account.Credentials:
    inline = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if inline:
        try:
            info = json.loads(inline)
        except json.JSONDecodeError as error:
            raise CredentialsError(
                "GOOGLE_SERVICE_ACCOUNT_JSON задана, но это не валидный JSON"
            ) from error
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return service_account.Credentials.from_service_account_file(
        str(credentials_path()), scopes=SCOPES
    )


@lru_cache(maxsize=1)
def service_account_email() -> str:
    """Email робота — именно ему нужно давать доступ к папкам и таблицам."""
    return get_credentials().service_account_email


def working_folder_id() -> str:
    """ID рабочей папки на Диске, где живут подборки клиентов."""
    folder_id = os.getenv("DRIVE_FOLDER_ID")
    if not folder_id:
        raise CredentialsError("Не задана переменная DRIVE_FOLDER_ID в .env")
    return folder_id


@lru_cache(maxsize=1)
def sheets_service():
    return build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)


@lru_cache(maxsize=1)
def drive_service():
    return build("drive", "v3", credentials=get_credentials(), cache_discovery=False)
