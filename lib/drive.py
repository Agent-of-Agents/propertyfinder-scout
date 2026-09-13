"""Работа с Google Drive через сервисный аккаунт.

Всё, что умеет модуль:
  - список файлов в рабочей папке        list_files
  - найти таблицу по имени               find_spreadsheet
  - создать таблицу в папке              create_spreadsheet_in_folder
  - переместить файл в папку             move_file
  - создать вложенную папку              ensure_folder
  - выдать доступ человеку               share_with

Про квоту: у сервисного аккаунта нет собственного хранилища на Диске.
Файл, который он создаёт, принадлежит ему — и упирается в нулевую квоту.
Поэтому create_spreadsheet_in_folder ловит эту ошибку и объясняет, что делать.
"""

from __future__ import annotations

from typing import Any

from googleapiclient.errors import HttpError

from .google_auth import drive_service, service_account_email, working_folder_id

SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
FOLDER_MIME = "application/vnd.google-apps.folder"

FIELDS = "id, name, mimeType, createdTime, modifiedTime, webViewLink, owners(emailAddress)"


class DriveQuotaError(RuntimeError):
    """Сервисный аккаунт пытается создать файл, но у него нет хранилища."""


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


# --------------------------------------------------------------------------- чтение


def list_files(folder_id: str | None = None, mime_type: str | None = None,
               page_size: int = 100) -> list[dict]:
    """Файлы в папке. По умолчанию — в рабочей папке из .env."""
    folder = folder_id or working_folder_id()
    query = [f"'{_escape(folder)}' in parents", "trashed = false"]
    if mime_type:
        query.append(f"mimeType = '{_escape(mime_type)}'")

    result = (
        drive_service()
        .files()
        .list(
            q=" and ".join(query),
            pageSize=page_size,
            fields=f"files({FIELDS})",
            orderBy="modifiedTime desc",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return result.get("files", [])


def get_file(file_id: str) -> dict:
    return (
        drive_service()
        .files()
        .get(fileId=file_id, fields=FIELDS + ", parents", supportsAllDrives=True)
        .execute()
    )


def find_spreadsheet(name: str, folder_id: str | None = None) -> dict | None:
    """Найти таблицу по точному имени. Возвращает None, если не найдена."""
    folder = folder_id or working_folder_id()
    query = (
        f"name = '{_escape(name)}' and "
        f"'{_escape(folder)}' in parents and "
        f"mimeType = '{SPREADSHEET_MIME}' and trashed = false"
    )
    result = (
        drive_service()
        .files()
        .list(
            q=query,
            pageSize=1,
            fields=f"files({FIELDS})",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = result.get("files", [])
    return files[0] if files else None


# --------------------------------------------------------------------------- создание


def create_spreadsheet_in_folder(name: str, folder_id: str | None = None) -> dict:
    """Создать Google-таблицу прямо в папке.

    Если сервисный аккаунт упрётся в отсутствие хранилища — поднимает
    DriveQuotaError с объяснением, как это чинится.
    """
    folder = folder_id or working_folder_id()
    metadata: dict[str, Any] = {
        "name": name,
        "mimeType": SPREADSHEET_MIME,
        "parents": [folder],
    }

    try:
        return (
            drive_service()
            .files()
            .create(body=metadata, fields=FIELDS, supportsAllDrives=True)
            .execute()
        )
    except HttpError as error:
        if "storageQuotaExceeded" in str(error) or "storage quota" in str(error).lower():
            raise DriveQuotaError(
                "Сервисный аккаунт не может создать файл: у него нет своего места на Диске.\n"
                "Два рабочих варианта:\n"
                "  1. Сделать папку Общим диском (Shared Drive) и добавить туда\n"
                f"     {service_account_email()} как участника с правами редактора.\n"
                "  2. Создать таблицу от имени Алексея, а сервисному аккаунту оставить\n"
                "     только редактирование ячеек — прав редактора ему достаточно."
            ) from error
        raise


def ensure_folder(name: str, parent_id: str | None = None) -> dict:
    """Найти или создать вложенную папку."""
    parent = parent_id or working_folder_id()
    query = (
        f"name = '{_escape(name)}' and '{_escape(parent)}' in parents and "
        f"mimeType = '{FOLDER_MIME}' and trashed = false"
    )
    existing = (
        drive_service()
        .files()
        .list(q=query, pageSize=1, fields=f"files({FIELDS})",
              supportsAllDrives=True, includeItemsFromAllDrives=True)
        .execute()
    ).get("files", [])
    if existing:
        return existing[0]

    return (
        drive_service()
        .files()
        .create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
            fields=FIELDS,
            supportsAllDrives=True,
        )
        .execute()
    )


# --------------------------------------------------------------------------- организация


def move_file(file_id: str, target_folder_id: str) -> dict:
    """Перенести файл в другую папку."""
    current = drive_service().files().get(
        fileId=file_id, fields="parents", supportsAllDrives=True
    ).execute()
    previous_parents = ",".join(current.get("parents", []))

    return (
        drive_service()
        .files()
        .update(
            fileId=file_id,
            addParents=target_folder_id,
            removeParents=previous_parents,
            fields=FIELDS + ", parents",
            supportsAllDrives=True,
        )
        .execute()
    )


def rename_file(file_id: str, new_name: str) -> dict:
    return (
        drive_service()
        .files()
        .update(fileId=file_id, body={"name": new_name}, fields=FIELDS, supportsAllDrives=True)
        .execute()
    )


def share_with(file_id: str, email: str, role: str = "writer",
               notify: bool = False) -> dict:
    """Выдать человеку доступ к файлу. role: reader | commenter | writer."""
    return (
        drive_service()
        .permissions()
        .create(
            fileId=file_id,
            body={"type": "user", "role": role, "emailAddress": email},
            sendNotificationEmail=notify,
            supportsAllDrives=True,
        )
        .execute()
    )


def trash_file(file_id: str) -> dict:
    """В корзину, не насовсем."""
    return (
        drive_service()
        .files()
        .update(fileId=file_id, body={"trashed": True}, fields=FIELDS, supportsAllDrives=True)
        .execute()
    )
