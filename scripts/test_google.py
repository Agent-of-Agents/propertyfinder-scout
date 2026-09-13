"""Приёмочный тест подключения к Google API через сервисный аккаунт.

Запуск:  .venv\\Scripts\\python.exe scripts\\test_google.py

Проверяет всю цепочку: авторизация → Drive → создание таблицы →
запись в A1 → чтение A1 обратно.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import drive, sheets  # noqa: E402
from lib.google_auth import (  # noqa: E402
    credentials_path,
    service_account_email,
    working_folder_id,
)

TEST_VALUE = "Google API работает"


def line(title: str) -> None:
    print(f"\n{'─' * 62}\n{title}\n{'─' * 62}")


def main() -> int:
    line("1. Авторизация")
    print(f"Ключ           : {credentials_path().relative_to(Path.cwd())}")
    print(f"Сервисный аккаунт: {service_account_email()}")
    print(f"Рабочая папка  : {working_folder_id()}")

    line("2. Google Drive — файлы в рабочей папке")
    try:
        files = drive.list_files()
    except Exception as error:  # noqa: BLE001
        print(f"ОШИБКА доступа к папке: {error}")
        print("\nПроверь, что папка расшарена на сервисный аккаунт с правами редактора.")
        return 1

    if files:
        for item in files:
            owner = (item.get("owners") or [{}])[0].get("emailAddress", "—")
            print(f"  • {item['name']}")
            print(f"    {item['mimeType'].split('.')[-1]} · владелец {owner}")
    else:
        print("  (папка пуста — это нормально для первого запуска)")

    line("3. Тестовая таблица")
    name = f"CLAUDE TEST {datetime.now():%Y-%m-%d %H:%M}"
    fallback_id = sys.argv[1] if len(sys.argv) > 1 else None

    try:
        created = drive.create_spreadsheet_in_folder(name)
        spreadsheet_id = created["id"]
        print(f"  Создана сервисным аккаунтом: {created['name']}")
        print(f"  ID     : {spreadsheet_id}")
        print(f"  Ссылка : {created.get('webViewLink')}")
    except drive.DriveQuotaError as error:
        print("  Создать своими силами сервисный аккаунт не может (ожидаемо):")
        print(f"  {str(error).splitlines()[0]}")
        if not fallback_id:
            print("\n  Передай ID уже существующей таблицы аргументом:")
            print("    .venv\\Scripts\\python.exe scripts\\test_google.py <spreadsheet_id>")
            return 2
        spreadsheet_id = fallback_id
        existing = drive.get_file(spreadsheet_id)
        owner = (existing.get("owners") or [{}])[0].get("emailAddress", "—")
        print(f"\n  Работаю с готовой таблицей: {existing['name']}")
        print(f"  Владелец: {owner}")
        print(f"  Ссылка  : {existing.get('webViewLink')}")
        created = existing
    except Exception as error:  # noqa: BLE001
        print(f"ОШИБКА: {error}")
        return 2

    line("4. Запись в A1")
    sheets.update_range(spreadsheet_id, "A1", [[TEST_VALUE]])
    print(f"  Записано: {TEST_VALUE!r}")

    line("5. Чтение A1 обратно")
    values = sheets.read_range(spreadsheet_id, "A1")
    read_back = values[0][0] if values and values[0] else None
    print(f"  Прочитано: {read_back!r}")

    line("6. Дополнительно — листы книги")
    for sheet in sheets.list_sheets(spreadsheet_id):
        print(f"  • {sheet['title']} (sheetId={sheet['sheet_id']}, "
              f"{sheet['rows']}×{sheet['columns']})")

    line("ИТОГ")
    if read_back == TEST_VALUE:
        print("✅ Вся цепочка работает: Drive + Sheets, чтение и запись.")
        print(f"\nТестовая таблица осталась в папке:\n{created.get('webViewLink')}")
        return 0

    print(f"❌ Прочитано не то, что записано: {read_back!r} вместо {TEST_VALUE!r}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
