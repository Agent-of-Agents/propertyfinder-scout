# propertyfinder-scout · Scout Dubai

Агент подбора недвижимости в Дубае для брокера Алексея Широкова: вторичка и проекты
застройщиков, книги Google на клиента, ежедневный прогон, Telegram-группа с темой
на клиента. Что и как он делает — `docs/HANDOFF.md`; как это выглядит в чате —
`docs/scout-dubai-telegram.html`.

* Модель: `anthropic:claude-opus-5`
* Стандарт: LangChain v1 `create_agent`
* Сгенерировано: agent-builder-mcp 2026-09-11T21:17:20+00:00

## Запуск

```bash
cp .env.example .env      # и заполнить ANTHROPIC_API_KEY
python main.py "твой запрос"
```

## Структура

| Файл | Что внутри |
|---|---|
| `agent.py` | `build_agent()` — сборка агента, системный промпт |
| `config.py` | `AgentConfig` — модель и лимиты из окружения |
| `tools/` | инструменты, реестр `TOOLS`: `catalog.py` (стандарт), `scout_clients`, `scout_objects`, `scout_ops` |
| `scout/` | прикладной слой: `store` (MongoDB / файлы), `models` (клиент → подбор), `books` (книги Google), `cards` (тексты и кнопки Telegram), `actions`, `runner` (прогон) |
| `lib/` | предметная логика: Property Finder, Google Sheets/Drive, сверка, дистресс, PDF, off-plan |
| `scripts/` | CLI для локального запуска (дневной прогон, презентации, планировки) |
| `bot.py` | Telegram: темы, кнопки, фото планировок, планировщик 08:00 |
| `middleware/` | ретраи, human-in-the-loop |
| `schemas.py` | pydantic-модель структурированного ответа |
| `main.py` | CLI |
| `tests/` | смоук-тесты |
| `agent.json` | манифест для деплоя |

## Запуск бота

```bash
cp .env.example .env      # токен бота, ALLOWED_USER_IDS, MONGODB_URI, GROUP_ID, ключ Google, DRIVE_FOLDER_ID
python bot.py
pytest -q                 # тесты без сети
```

## Как добавить инструмент

1. Новый модуль в `tools/`, функция с `@tool`, docstring и типами.
2. Импортировать его в `tools/__init__.py` и добавить в `TOOLS`.
3. Необратимое действие — вписать имя инструмента в `INTERRUPT_ON`
   в `middleware/__init__.py`.
