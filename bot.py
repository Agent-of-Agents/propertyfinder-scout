"""Telegram-бот агента «propertyfinder-scout» — то, что крутится в контейнере.

Запуск:  python bot.py

Нужно в окружении:
    TELEGRAM_BOT_TOKEN — токен от @BotFather
    ALLOWED_USER_IDS   — кому разрешено писать боту, через запятую
    MONGODB_URI        — память сессий; без неё диалог не переживёт перезапуск
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from agent import build_agent
from config import AgentConfig
from memory import build_checkpointer

TELEGRAM_LIMIT = 4096

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("propertyfinder-scout")

dp = Dispatcher()

# Пустой список = бот никого не пускает, но подскажет каждому его ID.
ALLOWED = {
    int(x)
    for x in os.environ.get("ALLOWED_USER_IDS", "").replace(" ", "").split(",")
    if x
}

# Счётчик «поколений» диалога на каждый чат — им работает /reset.
_generations: dict[int, int] = {}

_config = AgentConfig.from_env()
_agent = None


def chunks(text: str) -> list[str]:
    """Режет длинный ответ на куски, которые Telegram согласится отправить."""
    parts = [text[i : i + TELEGRAM_LIMIT] for i in range(0, len(text), TELEGRAM_LIMIT)]
    return parts or ["(пустой ответ)"]


def is_allowed(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id in ALLOWED


def thread_id(chat_id: int) -> str:
    """Идентификатор диалога для чекпоинтера: свой на чат и на поколение."""
    return f"tg-{chat_id}-{_generations.setdefault(chat_id, 0)}"


@dp.message(CommandStart())
async def on_start(message: Message) -> None:
    if not is_allowed(message):
        await message.answer(
            "Доступ закрыт.\n\n"
            f"Ваш Telegram ID: {message.from_user.id}\n"
            "Добавьте его в ALLOWED_USER_IDS и перезапустите бота."
        )
        return
    await message.answer(
        "propertyfinder-scout на связи.\n\n"
        "Агент-разведчик каталога недвижимости propertyfinder.ae: открывает страницы каталога и рассказывает, что на них видно.\n\n"
        "/reset — начать диалог заново"
    )


@dp.message(Command("reset"))
async def on_reset(message: Message) -> None:
    """Меняет thread_id, чтобы агент забыл предыдущий контекст диалога."""
    if not is_allowed(message):
        return
    _generations[message.chat.id] = _generations.get(message.chat.id, 0) + 1
    await message.answer("Контекст диалога очищен.")


@dp.message(F.text)
async def on_text(message: Message) -> None:
    if not is_allowed(message):
        await message.answer(f"Доступ закрыт. Ваш Telegram ID: {message.from_user.id}")
        return

    config = {"configurable": {"thread_id": thread_id(message.chat.id)}}

    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        try:
            result = await _agent.ainvoke(
                {"messages": [{"role": "user", "content": message.text}]}, config
            )
            answer = result["messages"][-1].text
        except Exception:
            log.exception("Ошибка при обработке сообщения")
            answer = "Что-то пошло не так при обращении к модели. Подробности в логе."

    for part in chunks(answer):
        await message.answer(part)


async def main() -> None:
    global _agent

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Нет TELEGRAM_BOT_TOKEN. Получите токен у @BotFather и положите его "
            "в .env рядом с docker-compose.yml."
        )
    _config.require_api_key()
    if not ALLOWED:
        log.warning("ALLOWED_USER_IDS пуст — бот никого не пустит, но покажет каждому его ID.")

    checkpointer = build_checkpointer(_config)
    if checkpointer is None:
        log.warning("MONGODB_URI не задан — история диалогов не переживёт перезапуск.")
    else:
        log.info("Память сессий: MongoDB, база %s", _config.mongodb_db)

    _agent = build_agent(_config, checkpointer=checkpointer)

    bot = Bot(token=token)
    me = await bot.get_me()
    log.info("Бот @%s запущен, модель %s", me.username, _config.model)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
