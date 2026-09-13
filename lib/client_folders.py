"""Папки клиентов на Google Диске.

Структура, как договорились с Алексеем:

    Подборки/
      Собитова София — Marina Shores/
        1BR/
        2BR/

Внутрь ложатся готовые презентации, а в таблицу идёт ссылка на нужную папку.
Папки сервисный аккаунт создавать умеет — хранилища они не занимают.
Файлы он загружать не может, для этого нужен Общий диск.
"""

from __future__ import annotations

from .drive import ensure_folder, list_files
from .google_auth import working_folder_id

BEDROOM_LABELS = {"studio": "Studio", "0": "Studio"}


def bedroom_folder_name(bedrooms: str | int) -> str:
    key = str(bedrooms).strip().lower()
    return BEDROOM_LABELS.get(key, f"{key.upper()}BR" if key.isdigit() else key.upper())


def client_folder_name(full_name: str, target: str) -> str:
    """«Собитова София — Marina Shores»."""
    return f"{full_name.strip()} — {target.strip()}" if target else full_name.strip()


def ensure_client_tree(full_name: str, target: str,
                       bedrooms: list[str]) -> dict[str, dict]:
    """Создать папку клиента и подпапки под каждый тип квартиры.

    Возвращает {'_client': папка клиента, '1BR': папка, '2BR': папка, ...}
    """
    client = ensure_folder(client_folder_name(full_name, target), working_folder_id())
    tree: dict[str, dict] = {"_client": client}

    for bedroom in bedrooms:
        label = bedroom_folder_name(bedroom)
        if label not in tree:
            tree[label] = ensure_folder(label, client["id"])

    return tree


def folder_link(folder: dict) -> str:
    return folder.get("webViewLink") or f"https://drive.google.com/drive/folders/{folder['id']}"


def describe(tree: dict[str, dict]) -> str:
    lines = [f"{tree['_client']['name']}  →  {folder_link(tree['_client'])}"]
    for label, folder in tree.items():
        if label != "_client":
            lines.append(f"  {label}  →  {folder_link(folder)}")
    return "\n".join(lines)


def existing_files(folder_id: str) -> list[dict]:
    return list_files(folder_id=folder_id)
