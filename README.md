# propertyfinder-scout

Агент-разведчик каталога недвижимости propertyfinder.ae: открывает страницы каталога и рассказывает, что на них видно.

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
| `tools/` | инструменты, реестр `TOOLS` |
| `middleware/` | ретраи, human-in-the-loop |
| `schemas.py` | pydantic-модель структурированного ответа |
| `main.py` | CLI |
| `tests/` | смоук-тесты |
| `agent.json` | манифест для деплоя |

## Как добавить инструмент

1. Новый модуль в `tools/`, функция с `@tool`, docstring и типами.
2. Импортировать его в `tools/__init__.py` и добавить в `TOOLS`.
3. Необратимое действие — вписать имя инструмента в `INTERRUPT_ON`
   в `middleware/__init__.py`.
