"""Telegram-бот агента «Scout Dubai» — то, что крутится в контейнере.

Запуск:  python bot.py

Нужно в окружении (стандарт фабрики):
    TELEGRAM_BOT_TOKEN — токен от @BotFather
    ALLOWED_USER_IDS   — кому разрешено писать боту, через запятую
    MONGODB_URI        — память сессий и состояние подборов; без неё ничего не переживёт перезапуск

Scout Dubai (docs/HANDOFF.md §7, §9):
    GROUP_ID           — супергруппа с темами: тема на клиента, General — сводка и брифы.
                         Пусто — работаем в личном чате, тег «Клиент · Подбор» в каждом сообщении.
    DRIVE_FOLDER_ID, GOOGLE_SERVICE_ACCOUNT_JSON — книги клиентов (см. lib/google_auth.py)
    TZ=Asia/Dubai, DAILY_RUN_AT=08:00, QUIET_HOURS=22:00-08:00

Разделение труда: свободный текст → агент (LangChain), кнопки, фото, расписание →
детерминированные действия scout.actions. Оба слоя не пишут в колонки Алексея
иначе как по его кнопке или ответу.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender

from agent import build_agent
from config import AgentConfig
from memory import build_checkpointer
from scout import actions, cards, outbox, runner, transcribe
from scout.cards import Outgoing
from scout.models import CLOSE_BOUGHT, CLOSE_DROPPED, MARKET_SECONDARY, SEARCH_ACTIVE, SEARCH_PAUSED
from scout.store import META, STATES, get_store

TELEGRAM_LIMIT = 4096
CAPTION_LIMIT = 1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scout-dubai")

dp = Dispatcher()

# Пустой список = бот никого не пускает, но подскажет каждому его ID.
ALLOWED = {
    int(x)
    for x in os.environ.get("ALLOWED_USER_IDS", "").replace(" ", "").split(",")
    if x
}
GROUP_ID = int(os.environ["GROUP_ID"]) if os.environ.get("GROUP_ID", "").strip() else None
TZ = ZoneInfo(os.environ.get("TZ") or "Asia/Dubai")
DAILY_RUN_AT = os.environ.get("DAILY_RUN_AT", "08:00")
QUIET_HOURS = os.environ.get("QUIET_HOURS", "22:00-08:00")

# Счётчик «поколений» диалога на каждый чат/тему — им работает /reset.
_generations: dict[str, int] = {}

_config = AgentConfig.from_env()
_agent = None
_bot: Bot | None = None


# ------------------------------------------------------------------ общее

def chunks(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Режет длинный ответ на куски, которые Telegram согласится отправить."""
    parts = [text[i: i + limit] for i in range(0, len(text), limit)]
    return parts or ["(пустой ответ)"]


def is_allowed(event: Message | CallbackQuery) -> bool:
    user = event.from_user
    if user is None or user.id not in ALLOWED:
        return False
    chat = event.chat if isinstance(event, Message) else (event.message.chat if event.message else None)
    if GROUP_ID is not None and chat is not None and chat.type != "private" and chat.id != GROUP_ID:
        return False
    return True


def topic_of(message: Message) -> int | None:
    """Тема форума, из которой пришло сообщение. None — General или личный чат."""
    if message.is_topic_message and message.message_thread_id:
        return message.message_thread_id
    return None


def dialog_key(chat_id: int, thread: int | None) -> str:
    key = f"tg-{chat_id}-{thread or 0}"
    return f"{key}-{_generations.setdefault(key, 0)}"


def keyboard(spec: Outgoing) -> InlineKeyboardMarkup | None:
    rows = []
    for row in spec.buttons:
        line = []
        for b in row:
            if b.url:
                line.append(InlineKeyboardButton(text=b.label[:64], url=b.url))
            else:
                line.append(InlineKeyboardButton(text=b.label[:64], callback_data=_fit_cb(b.data)))
        if line:
            rows.append(line)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _fit_cb(data: str) -> str:
    """callback_data ≤ 64 байт; длиннее — ключ в tg_map."""
    if len(data.encode("utf-8")) <= 64:
        return data
    key = f"k|{uuid.uuid4().hex[:12]}"
    actions.remember_message(key, {"data": data})
    return key


def _thread_kwargs(thread: int | None) -> dict:
    return {"message_thread_id": thread} if thread else {}


async def send(chat_id: int, spec: Outgoing, thread: int | None = None) -> Message | None:
    """Отправить Outgoing: фото с подписью, файл или текст, с кнопками; запомнить адрес объекта."""
    assert _bot is not None
    thread = spec.thread_id if spec.thread_id is not None else thread
    kwargs = {"chat_id": chat_id, "parse_mode": ParseMode.HTML, **_thread_kwargs(thread)}
    markup = keyboard(spec)
    sent: Message | None = None
    try:
        if spec.file_path and Path(spec.file_path).exists():
            sent = await _bot.send_document(document=FSInputFile(spec.file_path),
                                            caption=spec.text[:CAPTION_LIMIT], reply_markup=markup, **kwargs)
        elif spec.photo_url:
            caption, tail = _split_caption(spec.text)
            try:
                sent = await _bot.send_photo(photo=spec.photo_url, caption=caption,
                                             reply_markup=None if tail else markup, **kwargs)
            except TelegramBadRequest:
                sent, tail = None, ""   # фото не отдалось — вся карточка текстом
            if sent is None:
                sent = await _send_text(spec.text, markup, kwargs)
            elif tail:
                sent = await _send_text(tail, markup, kwargs)
        else:
            sent = await _send_text(spec.text, markup, kwargs)
    except TelegramBadRequest as error:
        log.warning("Telegram отклонил сообщение (%s), шлю без разметки", error)
        sent = await _bot.send_message(chat_id=chat_id, text=re.sub(r"<[^>]+>", "", spec.text)[:TELEGRAM_LIMIT],
                                       reply_markup=markup, **_thread_kwargs(thread))
    if sent and spec.meta.get("listing"):
        actions.remember_message(f"m|{chat_id}|{sent.message_id}", spec.meta)
    return sent


async def _send_text(text: str, markup, kwargs: dict) -> Message | None:
    parts = chunks(text)
    sent = None
    for i, part in enumerate(parts):
        sent = await _bot.send_message(text=part, reply_markup=markup if i == len(parts) - 1 else None,
                                       disable_web_page_preview=True, **kwargs)
    return sent


def _split_caption(text: str) -> tuple[str, str]:
    if len(text) <= CAPTION_LIMIT:
        return text, ""
    cut = text.rfind("\n", 0, CAPTION_LIMIT - 1)
    cut = cut if cut > 200 else CAPTION_LIMIT - 1
    return text[:cut], text[cut:].lstrip("\n")


async def flush_outbox(chat_id: int, thread: int | None) -> None:
    for spec in outbox.drain():
        await send(chat_id, spec, thread)


async def run_blocking(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


def topic_link(client) -> str:
    if GROUP_ID is None or not client.telegram_topic_id:
        return ""
    internal = str(GROUP_ID).replace("-100", "", 1)
    return f"https://t.me/c/{internal}/{client.telegram_topic_id}"


def in_quiet_hours(now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now(TZ)
    try:
        start_s, end_s = QUIET_HOURS.split("-")
        start, end = dt.time.fromisoformat(start_s), dt.time.fromisoformat(end_s)
    except ValueError:
        return False
    t = now.time()
    return (t >= start or t < end) if start > end else (start <= t < end)


def book_link(client) -> str:
    return f"https://docs.google.com/spreadsheets/d/{client.spreadsheet_id}"


def model_error_text(error: Exception) -> str:
    """Причина сбоя модели — в чат, человеческим языком, а не «смотри лог»."""
    text = str(error)
    low = text.lower()
    if "credit balance" in low or "billing" in low:
        return ("⚠️ Модель недоступна: на счёте Anthropic API закончились средства. "
                "Пополни баланс в console.anthropic.com → Plans & Billing и повтори сообщение.")
    if "rate_limit" in low or "429" in low:
        return "⚠️ Модель отвечает «слишком много запросов» — подожди минуту и повтори."
    if "overloaded" in low or "529" in low:
        return "⚠️ Сервис Anthropic перегружен — повтори через пару минут."
    if "authentication" in low or "401" in low or "invalid x-api-key" in low:
        return "⚠️ Модель не принимает ключ ANTHROPIC_API_KEY — проверь его в .env."
    if "timeout" in low or "timed out" in low:
        return "⚠️ Модель не ответила вовремя — повтори сообщение."
    short = text.split("\n")[0][:200]
    return f"⚠️ Модель не ответила: {short}. Подробности в логе."


# ------------------------------------------------------------------ контекст для агента

async def resolve_context(message: Message):
    """Клиент по теме, из которой пришло сообщение (None — General / личный чат)."""
    thread = topic_of(message)
    if thread is None:
        return None
    return await run_blocking(actions.client_by_topic, thread)


def context_prefix(client) -> str:
    return "[General]" if client is None else f"[тема: {client.slug} — {client.name}]"


# ------------------------------------------------------------------ команды

@dp.message(CommandStart())
async def on_start(message: Message) -> None:
    if not is_allowed(message):
        await message.answer(
            "Доступ закрыт.\n\n"
            f"Ваш Telegram ID: {message.from_user.id}\n"
            "Добавьте его в ALLOWED_USER_IDS и перезапустите бота."
        )
        return
    mode = ("группа с темами" if GROUP_ID
            else "личный чат — тема на клиента недоступна, в сообщениях будет тег «Клиент · Подбор»")
    await message.answer(
        "Scout Dubai на связи.\n\n"
        f"Режим: {mode}.\n"
        "Бриф нового клиента — текстом в General. Вопросы по клиенту — в его теме.\n\n"
        "/clients — клиенты и подборы\n/status — последний прогон\n/run — прогон сейчас\n"
        "/panel — книга «Панель»\n/pause, /resume, /close — в теме клиента\n/reset — начать диалог заново"
    )


@dp.message(Command("reset"))
async def on_reset(message: Message) -> None:
    """Меняет thread_id, чтобы агент забыл предыдущий контекст диалога."""
    if not is_allowed(message):
        return
    key = f"tg-{message.chat.id}-{topic_of(message) or 0}"
    _generations[key] = _generations.get(key, 0) + 1
    await message.answer("Контекст диалога очищен.")


@dp.message(Command("clients"))
async def on_clients(message: Message) -> None:
    if not is_allowed(message):
        return
    items = await run_blocking(actions.clients_overview)
    spec = cards.clients_list(items)
    rows = []
    for i in items:
        if i["status"] == "active":
            rows.append([
                InlineKeyboardButton(text=f"⏸ {i['name'][:20]}", callback_data=cards.cb(cards.ACT_PAUSE, i["slug"])),
                InlineKeyboardButton(text="✔ Куплено", callback_data=cards.cb(cards.ACT_CONFIRM, i["slug"], "", CLOSE_BOUGHT)),
                InlineKeyboardButton(text="✕ Закрыть", callback_data=cards.cb(cards.ACT_CONFIRM, i["slug"], "", CLOSE_DROPPED)),
            ])
        else:
            rows.append([InlineKeyboardButton(text=f"▶ {i['name'][:24]}", callback_data=cards.cb(cards.ACT_RESUME, i["slug"]))])
    await message.answer(spec.text, parse_mode=ParseMode.HTML,
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


@dp.message(Command("panel"))
async def on_panel(message: Message) -> None:
    if not is_allowed(message):
        return
    try:
        link = await run_blocking(actions.panel_update, None, topic_link)
        await message.answer(f"📊 Панель: {link}")
    except Exception as error:  # noqa: BLE001
        await message.answer(f"⚠️ {error}")


@dp.message(Command("status"))
async def on_status(message: Message) -> None:
    if not is_allowed(message):
        return
    store = get_store()
    lines = [f"Хранилище: {store.backend} · Google: {'настроен' if actions.google_configured() else 'не настроен'}"
             f" · группа: {GROUP_ID or 'нет'} · прогон {DAILY_RUN_AT} {TZ.key}",
             f"Голос: {transcribe.available() or 'не настроен'}",
             f"Этот чат: {message.chat.id} ({message.chat.type})"
             + (" — это значение и есть GROUP_ID для .env" if message.chat.type == "supergroup" and GROUP_ID is None else "")]
    for client in await run_blocking(actions.list_clients):
        for s in await run_blocking(actions.client_searches, client):
            rep = (store.get(STATES, s.key) or {}).get("last_report") or {}
            lines.append(f"{client.short_name} · {s.title} · {s.status} · прогон {rep.get('run_at', '—')}"
                         + (f" · ⚠️ {rep['error']}" if rep.get("error") else f" · объектов {rep.get('total', '—')}"))
    await message.answer("\n".join(lines))


@dp.message(Command("run"))
async def on_run(message: Message) -> None:
    if not is_allowed(message):
        return
    client = await resolve_context(message)
    await message.answer("Запускаю прогон" + (f" по {client.short_name}" if client else "") + "…")
    await daily_job(only=client.slug if client else None, announce_chat=message.chat.id)


@dp.message(Command("pause"))
async def on_pause(message: Message) -> None:
    await _set_client_status(message, SEARCH_PAUSED)


@dp.message(Command("resume"))
async def on_resume(message: Message) -> None:
    await _set_client_status(message, SEARCH_ACTIVE)


async def _set_client_status(message: Message, status: str) -> None:
    if not is_allowed(message):
        return
    client = await resolve_context(message)
    if client is None:
        await message.answer("Эта команда — в теме клиента.")
        return
    for s in await run_blocking(actions.client_searches, client):
        if s.status in (SEARCH_ACTIVE, SEARCH_PAUSED):
            await run_blocking(actions.set_search_status, client, s, status)
    word = "на паузе — прогон остановлен, книга сохранена" if status == SEARCH_PAUSED else "снова активен"
    await message.answer(f"{client.short_name} {word}.")


@dp.message(Command("close"))
async def on_close(message: Message) -> None:
    if not is_allowed(message):
        return
    client = await resolve_context(message)
    if client is None:
        await message.answer("Эта команда — в теме клиента.")
        return
    active = await run_blocking(actions.client_searches, client, True)
    if not active:
        await message.answer("Активных подборов нет.")
        return
    rows = [[InlineKeyboardButton(text=f"✔ Куплено · {s.title}"[:60],
                                  callback_data=cards.cb(cards.ACT_CONFIRM, client.slug, s.slug, CLOSE_BOUGHT)),
             InlineKeyboardButton(text=f"✕ Закрыть · {s.title}"[:60],
                                  callback_data=cards.cb(cards.ACT_CONFIRM, client.slug, s.slug, CLOSE_DROPPED))]
            for s in active]
    rows.append([InlineKeyboardButton(text="✖ Отмена", callback_data=cards.cb(cards.ACT_CANCEL))])
    await message.answer(f"{client.short_name}: какой подбор закрыть?",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


# ------------------------------------------------------------------ текст → агент (или ответ на вопрос бота)

@dp.message(F.text)
async def on_text(message: Message) -> None:
    if not is_allowed(message):
        await message.answer(f"Доступ закрыт. Ваш Telegram ID: {message.from_user.id}")
        return
    await handle_text(message, message.text or "")


async def handle_text(message: Message, text: str) -> None:
    """Текст от Алексея — набранный или расшифрованный из голосового."""
    thread = topic_of(message)

    # Ждём от Алексея цену? Тогда это ответ боту, не агенту.
    pending = await run_blocking(actions.pop_pending, f"{message.chat.id}|{thread or 0}")
    if pending and pending.get("kind") == "price":
        await _handle_price_answer(message, pending, text)
        return

    client = await resolve_context(message)
    prompt = f"{context_prefix(client)}\n{text}"
    config = {"configurable": {"thread_id": dialog_key(message.chat.id, thread)}}

    outbox.begin()
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id, message_thread_id=thread):
        try:
            result = await _agent.ainvoke({"messages": [{"role": "user", "content": prompt}]}, config)
            answer = result["messages"][-1].text
        except Exception as error:  # noqa: BLE001
            log.exception("Ошибка при обработке сообщения")
            answer = model_error_text(error)

    for part in chunks(answer):
        await message.answer(part)
    await flush_outbox(message.chat.id, thread)


async def _handle_price_answer(message: Message, pending: dict, text: str) -> None:
    digits = re.sub(r"[^\d]", "", text or "")
    if not digits:
        await run_blocking(actions.set_pending, f"{message.chat.id}|{topic_of(message) or 0}", pending)
        await message.answer("Жду цену числом, например 3 720 000. Или нажми «Отмена» под вопросом.")
        return
    price = int(digits)
    client = await run_blocking(actions.get_client, pending["client"])
    search = await run_blocking(actions.get_search, pending["client"], pending["search"])
    await message.answer(f"✅ Одобряю · цена {cards.money(price)} — пишу в лист и собираю презентацию…")
    try:
        async with ChatActionSender.upload_document(bot=message.bot, chat_id=message.chat.id,
                                                    message_thread_id=topic_of(message)):
            result = await run_blocking(actions.approve, client, search, pending["listing"], price)
    except Exception as error:  # noqa: BLE001
        log.exception("Одобрение")
        await message.answer(f"⚠️ Не вышло: {error}")
        return
    result["listing"] = pending["listing"]
    await send(message.chat.id, cards.pdf_card(client, search, result), topic_of(message))


# ------------------------------------------------------------------ фото: планировка от брокера

@dp.message(F.photo | F.document)
async def on_photo(message: Message) -> None:
    if not is_allowed(message):
        return
    if message.document and not (message.document.mime_type or "").startswith("image/"):
        return
    thread = topic_of(message)
    client = await resolve_context(message)
    address = None
    if message.reply_to_message:
        address = await run_blocking(actions.recall_message,
                                     f"m|{message.chat.id}|{message.reply_to_message.message_id}")
    if address is None and client is not None:
        address = await run_blocking(_match_by_hint, client, message.caption or "")
    if address is None:
        await message.answer("К какому объекту это? Ответь этой фотографией на карточку объекта "
                             "или напиши в подписи брокера, агентство, цену или ID.")
        return

    file = message.photo[-1] if message.photo else message.document
    suffix = ".png" if message.document and "png" in (message.document.mime_type or "") else ".jpg"
    tmp = Path(tempfile.gettempdir()) / f"tg_{file.file_unique_id}{suffix}"
    await message.bot.download(file, destination=tmp)
    caption = (message.caption or "").lower()
    as_photo = "фото" in caption and "план" not in caption

    client = client or await run_blocking(actions.get_client, address["client"])
    search = await run_blocking(actions.get_search, address["client"], address["search"])
    try:
        result = await run_blocking(actions.attach_plan, client, search, address["listing"], tmp, as_photo)
    except Exception as error:  # noqa: BLE001
        log.exception("Планировка")
        await message.answer(f"⚠️ Не приложил: {error}")
        return
    what = "Фото" if as_photo else "Планировка"
    if result.get("rebuilt"):
        result["summary"] = f"{what} приложена, презентация пересобрана"
        await send(message.chat.id, cards.pdf_card(client, search, result), thread)
    else:
        await message.answer(f"{what} приложена к объекту {address['listing']}"
                             + (" и лежит в папке объекта на Диске" if result.get("folder_link") else "")
                             + ". Презентации ещё нет — соберу, когда одобришь с ценой.")


def _match_by_hint(client, hint: str) -> dict | None:
    """Зацепка из подписи — брокер, агентство, цена, ID — против выгрузок активных подборов."""
    hint = hint.strip().lower()
    if not hint:
        return None
    digits = re.sub(r"[^\d]", "", hint)
    for s in actions.client_searches(client, active_only=True):
        for row in actions.raw_rows(s):
            hay = " ".join(str(row.get(k, "")) for k in ("agent_name", "agency", "id", "agency_phone")).lower()
            if hint in hay or (digits and (digits in str(row.get("price", "")) or digits in str(row.get("id", "")))):
                return {"client": client.slug, "search": s.slug, "listing": row["id"]}
    return None


@dp.message(F.voice | F.audio | F.video_note)
async def on_voice(message: Message) -> None:
    """Голосовое, аудиофайл или кружок → Whisper → тот же путь, что у текста."""
    if not is_allowed(message):
        return
    if not transcribe.available():
        await message.answer("Голос пока не распознаю: в .env нет OPENAI_API_KEY. Продиктуй текстом "
                             "или нажми в Telegram «→ в текст» и перешли расшифровку.")
        return
    media = message.voice or message.audio or message.video_note
    if message.voice:
        suffix = ".oga"
    elif message.video_note:
        suffix = ".mp4"
    else:
        suffix = Path(getattr(media, "file_name", "") or "a.mp3").suffix or ".mp3"
    tmp = Path(tempfile.gettempdir()) / f"tg_{media.file_unique_id}{suffix}"
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id,
                                       message_thread_id=topic_of(message)):
        try:
            await message.bot.download(media, destination=tmp)
            text = await run_blocking(transcribe.transcribe, tmp)
        except transcribe.TranscribeError as error:
            await message.answer(f"⚠️ {error}")
            return
        except Exception as error:  # noqa: BLE001
            log.exception("Распознавание")
            await message.answer(f"⚠️ Голос не распознан: {type(error).__name__}: {str(error)[:200]}")
            return
        finally:
            tmp.unlink(missing_ok=True)
    if not text:
        await message.answer("В записи не расслышал слов — повтори, пожалуйста.")
        return
    await message.answer(f"🎤 <i>{cards.esc(text)}</i>", parse_mode=ParseMode.HTML)
    await handle_text(message, text)


# ------------------------------------------------------------------ кнопки

@dp.callback_query()
async def on_callback(call: CallbackQuery) -> None:
    if not is_allowed(call):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    data = call.data or ""
    if data.startswith("k|"):
        stored = await run_blocking(actions.recall_message, data)
        data = (stored or {}).get("data", "")
    parsed = cards.parse_cb(data)
    if not parsed or call.message is None:
        await call.answer()
        return
    msg = call.message
    chat_id = msg.chat.id
    thread = msg.message_thread_id if msg.is_topic_message else None
    act = parsed["action"]
    try:
        await call.answer()
        handler = CALLBACKS.get(act)
        if handler is None:
            await msg.answer("Эта кнопка пока ничего не делает.")
            return
        await handler(call, parsed, chat_id, thread)
    except actions.NotConfigured as error:
        await msg.answer(f"⚠️ {error}")
    except Exception as error:  # noqa: BLE001
        log.exception("Кнопка %s", act)
        await msg.answer(f"⚠️ Не вышло: {type(error).__name__}: {str(error)[:300]}")


async def _clear_buttons(call: CallbackQuery) -> None:
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


async def cb_launch(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    """«✅ Запустить»: клиент, книга, тема, первый подбор, первый сбор."""
    draft = await run_blocking(actions.get_draft, p["client"])
    await _clear_buttons(call)
    await call.message.answer("Завожу клиента: книга, папка, тема… первый сбор займёт несколько минут.")
    client = await run_blocking(actions.create_client, draft["client"])
    topic_thread = None
    if GROUP_ID is not None:
        try:
            topic = await _bot.create_forum_topic(chat_id=GROUP_ID, name=client.name[:128])
            topic_thread = topic.message_thread_id
            await run_blocking(actions.set_topic, client, topic_thread)
        except TelegramBadRequest as error:
            log.warning("Тема не создана: %s", error)
            await call.message.answer(f"⚠️ Тему создать не смог ({error}). Боту нужны права администратора "
                                      "с управлением темами. Работаю без темы.")
    await run_blocking(actions.drop_draft, draft["id"])
    link = topic_link(client)
    await call.message.answer(
        f"Завёл <b>{cards.esc(client.name)}</b>: книга и папка на Диске"
        + (f', тема <a href="{link}">{cards.esc(client.name)} →</a>' if link else "")
        + ". Собираю первый подбор — напишу " + ("туда." if link else "сюда."),
        parse_mode=ParseMode.HTML)
    await _create_search_and_report(client, draft["search"], chat_id, topic_thread or thread)


async def _create_search_and_report(client, brief: dict, chat_id: int, thread: int | None, old=None) -> None:
    if old is not None:
        search, report = await run_blocking(actions.replace_search, client, old, brief)
        await send(chat_id, cards.plain(
            f"Закрыл <b>{cards.esc(old.title)}</b>: лист переименован в <code>✕ {cards.esc(old.title)}</code>, "
            f"серый, в конце книги — всё, что там было, осталось. Открыл <b>{cards.esc(search.title)}</b>."), thread)
    else:
        search, report = await run_blocking(actions.create_search, client, brief)
    if not search.sources and search.market == MARKET_SECONDARY:
        await send(chat_id, cards.warn(f"У подбора «{cards.esc(search.title)}» нет ссылки на страницу PF — "
                                       "пришли её в теме, и я запущу сбор."), thread)
        return
    if report.get("error"):
        await send(chat_id, cards.warn(f"{cards.esc(cards.tag(client, search))}: {cards.esc(report['error'])}"), thread)
        return
    await send(chat_id, cards.first_collection_card(client, search, report, book_link(client)), thread)


async def cb_edit(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    await run_blocking(actions.drop_draft, p["client"])
    await _clear_buttons(call)
    await call.message.answer("Напиши, что поправить — соберу карточку заново.")


async def cb_replace(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    draft = await run_blocking(actions.get_draft, p["client"])
    client = await run_blocking(actions.get_client, draft["client_slug"])
    old = await run_blocking(actions.get_search, client.slug, p["search"])
    await _clear_buttons(call)
    await run_blocking(actions.drop_draft, draft["id"])
    await _create_search_and_report(client, draft["search"], chat_id, client.telegram_topic_id or thread, old=old)


async def cb_add(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    draft = await run_blocking(actions.get_draft, p["client"])
    client = await run_blocking(actions.get_client, draft["client_slug"])
    await _clear_buttons(call)
    await run_blocking(actions.drop_draft, draft["id"])
    await call.message.answer("Добавляю параллельный подбор, собираю…")
    await _create_search_and_report(client, draft["search"], chat_id, client.telegram_topic_id or thread)


async def cb_cancel(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    await _clear_buttons(call)
    if p["client"] and not p["search"] and not p["listing"]:
        try:
            await run_blocking(actions.drop_draft, p["client"])      # отмена черновика
        except actions.NotFound:
            pass
    elif p["listing"]:
        pend = await run_blocking(actions.pop_pending, f"{chat_id}|{thread or 0}")
        if not pend:                                                    # «Передумал» под запросом брокеру
            client = await run_blocking(actions.get_client, p["client"])
            search = await run_blocking(actions.get_search, p["client"], p["search"])
            try:
                await run_blocking(actions.cancel_request, client, search, p["listing"])
            except Exception as error:  # noqa: BLE001
                log.info("Снять галочку запроса не вышло: %s", error)
    await call.message.answer("Отменил.")


async def cb_approve(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    client = await run_blocking(actions.get_client, p["client"])
    search = await run_blocking(actions.get_search, p["client"], p["search"])
    row = await run_blocking(actions.raw_row, search, p["listing"])
    if search.market != MARKET_SECONDARY:
        await run_blocking(actions.approve, client, search, p["listing"], None)
        await call.message.answer(f"✅ Одобрено · {row.get('project', p['listing'])}. Презентация по проекту — "
                                  "следующей задачей; брошюра застройщика — в листе.")
        return
    await run_blocking(actions.set_pending, f"{chat_id}|{thread or 0}",
                       {"kind": "price", "client": client.slug, "search": search.slug, "listing": p["listing"]})
    await send(chat_id, cards.ask_price_card(client, search, row), thread)


async def cb_request(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    client = await run_blocking(actions.get_client, p["client"])
    search = await run_blocking(actions.get_search, p["client"], p["search"])
    result = await run_blocking(actions.request_broker, client, search, p["listing"])
    await send(chat_id, cards.whatsapp_card(client, search, result["row"], result["link"]), thread)


async def cb_sent(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    client = await run_blocking(actions.get_client, p["client"])
    search = await run_blocking(actions.get_search, p["client"], p["search"])
    stamp = await run_blocking(actions.mark_sent, client, search, p["listing"])
    await _clear_buttons(call)
    await call.message.answer(f"Записал: <b>Запрошено {stamp}</b>. Когда брокер пришлёт планировку — "
                              "ответь фотографией на карточку объекта.", parse_mode=ParseMode.HTML)


async def cb_skip(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    client = await run_blocking(actions.get_client, p["client"])
    search = await run_blocking(actions.get_search, p["client"], p["search"])
    await run_blocking(actions.skip_listing, search, p["listing"])
    await _clear_buttons(call)
    await _show_new(client, search, chat_id, thread, count=1)


async def cb_show_new(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    client = await run_blocking(actions.get_client, p["client"])
    search = await run_blocking(actions.get_search, p["client"], p["search"])
    await _show_new(client, search, chat_id, thread, count=3)


async def _show_new(client, search, chat_id: int, thread: int | None, count: int) -> None:
    pending = await run_blocking(actions.pending_cards, client, search)
    if not pending:
        await send(chat_id, cards.plain(f"{cards.tagline(client, search)}Новых непоказанных объектов нет."), thread)
        return
    batch = pending[:count]
    for i, row in enumerate(batch, start=1):
        enriched = await run_blocking(actions.enrich_for_card, search, row)
        card = (cards.listing_card if search.market == MARKET_SECONDARY else cards.project_card)(
            client, search, enriched, position=f"{i} из {len(pending)}")
        await send(chat_id, card, thread)
    await run_blocking(actions.mark_shown, search, [r["id"] for r in batch])


async def cb_show_prices(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    client = await run_blocking(actions.get_client, p["client"])
    search = await run_blocking(actions.get_search, p["client"], p["search"])
    changes = await run_blocking(actions.price_changes, search)
    if not changes:
        await send(chat_id, cards.plain(f"{cards.tagline(client, search)}Изменений цены в последнем прогоне нет."), thread)
        return
    lines = [cards.tagline(client, search), f"<b>{cards.ICON_PRICE} Изменения цены</b>"]
    for c in changes[:15]:
        diff = f"{(int(c.get('new', 0)) - int(c.get('old', 0))):+,}".replace(",", " ")
        lines.append(f"{cards.esc(c.get('id'))} · {cards.money(c.get('old'))} → <b>{cards.money(c.get('new'))}</b>"
                     f" ({diff}) · {cards.esc(c.get('agent', ''))}")
    await send(chat_id, Outgoing("\n".join(lines)), thread)


async def cb_rebuild(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    client = await run_blocking(actions.get_client, p["client"])
    search = await run_blocking(actions.get_search, p["client"], p["search"])
    view = await run_blocking(actions.SheetRows, client, search)
    n = view.row_number(p["listing"])
    price = actions.parse_int(view.cell(view.rows[n - 2], "Моя цена"))
    if not price:
        await call.message.answer("В листе нет твоей цены для этого объекта — без неё презентацию не собираю.")
        return
    await call.message.answer("Пересобираю…")
    result = await run_blocking(actions.build_pdf, client, search, p["listing"], price)
    result.update({"listing": p["listing"], "price": price})
    await send(chat_id, cards.pdf_card(client, search, result), thread)


async def cb_confirm(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    """Закрыть подбор: listing здесь несёт причину — bought или dropped."""
    reason = p["listing"] if p["listing"] in (CLOSE_BOUGHT, CLOSE_DROPPED) else CLOSE_DROPPED
    client = await run_blocking(actions.get_client, p["client"])
    searches = await run_blocking(actions.client_searches, client, True)
    targets = [s for s in searches if not p["search"] or s.slug == p["search"]]
    await _clear_buttons(call)
    for s in targets:
        await run_blocking(actions.close_search, client, s, reason)
    mark = "✓ куплено" if reason == CLOSE_BOUGHT else "✕ закрыт"
    names = ", ".join(f"«{s.title}»" for s in targets) or "—"
    client = await run_blocking(actions.get_client, client.slug)
    text = f"{mark}: {names}. Листы переименованы, прогон по ним остановлен."
    if client.status != "active":
        text += " Все подборы закрыты — клиент в архиве, тема закрывается."
        if GROUP_ID is not None and client.telegram_topic_id:
            try:
                await _bot.close_forum_topic(chat_id=GROUP_ID, message_thread_id=client.telegram_topic_id)
            except TelegramBadRequest as error:
                log.warning("Тема не закрыта: %s", error)
    await call.message.answer(text)


async def cb_pause_resume(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    status = SEARCH_PAUSED if p["action"] == cards.ACT_PAUSE else SEARCH_ACTIVE
    client = await run_blocking(actions.get_client, p["client"])
    for s in await run_blocking(actions.client_searches, client):
        if s.status in (SEARCH_ACTIVE, SEARCH_PAUSED):
            await run_blocking(actions.set_search_status, client, s, status)
    await call.message.answer(f"{client.short_name}: {'⏸ пауза' if status == SEARCH_PAUSED else '▶ возобновлён'}.")


async def cb_clients(call: CallbackQuery, p: dict, chat_id: int, thread: int | None) -> None:
    await on_clients(call.message)


CALLBACKS = {
    cards.ACT_LAUNCH: cb_launch, cards.ACT_EDIT: cb_edit, cards.ACT_REPLACE: cb_replace, cards.ACT_ADD: cb_add,
    cards.ACT_CANCEL: cb_cancel, cards.ACT_APPROVE: cb_approve, cards.ACT_REQUEST: cb_request,
    cards.ACT_SENT: cb_sent, cards.ACT_SKIP: cb_skip, cards.ACT_SHOW_NEW: cb_show_new,
    cards.ACT_SHOW_PRICES: cb_show_prices, cards.ACT_REBUILD: cb_rebuild, cards.ACT_CONFIRM: cb_confirm,
    cards.ACT_PAUSE: cb_pause_resume, cards.ACT_RESUME: cb_pause_resume, cards.ACT_CLIENTS: cb_clients,
}


# ------------------------------------------------------------------ планировщик: 08:00 по Дубаю

async def daily_job(only: str | None = None, announce_chat: int | None = None) -> None:
    """Прогон → подробности в темы → PDF по галочкам → напоминания → сводка в General."""
    home = GROUP_ID or announce_chat or (next(iter(ALLOWED)) if ALLOWED else None)
    if home is None:
        log.warning("Некому писать дайджест: нет GROUP_ID и ALLOWED_USER_IDS")
        return
    if not actions.google_configured():
        await send(home, cards.warn("Прогон пропущен: Google не настроен (DRIVE_FOLDER_ID, ключ сервисного аккаунта)."))
        return
    try:
        result = await run_blocking(actions.daily, None, only)
    except Exception as error:  # noqa: BLE001
        log.exception("Дневной прогон")
        await send(home, cards.warn(f"Прогон не прошёл: {cards.esc(str(error)[:300])}. Повторю по расписанию."))
        return

    general_rows, paused, deadlines = [], [], []
    today = dt.date.today()
    for slug, entry in result.items():
        if slug.startswith("_"):
            await send(home, cards.warn(f"Каталог проектов: {cards.esc(entry.get('error', ''))}"))
            continue
        client = entry["client"]
        thread = client.telegram_topic_id if GROUP_ID else None
        searches = await run_blocking(actions.client_searches, client)
        by_slug = {s.slug: s for s in searches}
        for s_slug, rep in entry["searches"].items():
            s = by_slug[s_slug]
            changed = bool(rep.get("new") or rep.get("price") or rep.get("removed") or rep.get("changed") or rep.get("error"))
            general_rows.append({"client": client.short_name, "search": s.title, "icon": s.icon, "changed": changed,
                                 "new": len(rep.get("new", [])), "price": len(rep.get("price", [])),
                                 "removed": len(rep.get("removed", [])), "changed_projects": len(rep.get("changed", [])),
                                 "distress": len(entry.get("distress", []))})
            if rep.get("error"):
                await send(home, cards.warn(f"{cards.esc(client.short_name)} · {cards.esc(s.title)}: {cards.esc(rep['error'])}"))
                continue
            if changed:
                rep_for_card = dict(rep)
                if entry.get("distress"):
                    rep_for_card["distress"] = f"{len(entry['distress'])} новых совпадений"
                await send(home, cards.search_digest(client, s, rep_for_card, book_link(client)), thread)
            if rep.get("approved_removed"):
                ids = ", ".join(rep["approved_removed"])
                await send(home, cards.warn(
                    f"{cards.esc(client.short_name)} · {cards.esc(s.title)}: одобренный объект снят ({cards.esc(ids)}) — "
                    "второй день нет в выдаче. Строку не трогал, PDF остаётся. Стоит спросить брокера напрямую."), thread)
            if s.market == MARKET_SECONDARY:
                await _build_pending_pdfs(client, s, home, thread)
        for err in entry.get("errors", []):
            await send(home, cards.warn(f"{cards.esc(client.short_name)}: {cards.esc(err)}"), thread)
        live = [s for s in searches if s.status != "closed"]
        if live and all(s.status == SEARCH_PAUSED for s in live):
            paused.append(client.short_name)
        await _deadline_reminder(client, entry, by_slug, today, home, thread, deadlines)

    await send(home, cards.general_digest(general_rows, paused, deadlines))
    try:
        await run_blocking(actions.panel_update, None, topic_link)
    except Exception as error:  # noqa: BLE001
        log.warning("Панель: %s", error)


async def _build_pending_pdfs(client, search, home: int, thread: int | None) -> None:
    """Галочки ✅ с ценой, поставленные руками в таблице, без PDF — собираем и присылаем."""
    try:
        todo = await run_blocking(actions.approved_without_pdf, client, search)
        for item in todo:
            if not item["price"]:
                continue
            res = await run_blocking(actions.build_pdf, client, search, item["id"], item["price"])
            res.update({"listing": item["id"], "price": item["price"]})
            await send(home, cards.pdf_card(client, search, res), thread)
    except Exception as error:  # noqa: BLE001
        log.exception("PDF по галочкам %s", search.key)
        await send(home, cards.warn(f"PDF по галочкам {cards.esc(search.title)}: {cards.esc(str(error)[:200])}"), thread)


async def _deadline_reminder(client, entry, by_slug, today, home, thread, deadlines: list[str]) -> None:
    if not client.deadline:
        return
    try:
        days = (dt.date.fromisoformat(client.deadline) - today).days
    except ValueError:
        return
    if days < 0:
        return
    deadlines.append(f"{client.short_name} через {days} дн.")
    if days in (7, 2):
        lines = [f"{cards.ICON_DATE} <b>{cards.esc(client.short_name)}: сделка через {days} дней</b> · {cards.esc(client.deadline)}"]
        for s_slug, st in (entry.get("stats") or {}).items():
            s = by_slug.get(s_slug)
            if s:
                lines.append(f"{cards.esc(s.title)}: одобрено {st.get('approved', 0)}, презентаций {st.get('pdf', 0)}, "
                             f"запрошено {st.get('requested', 0)}")
        await send(home, cards.plain("\n".join(lines)), thread)


async def scheduler() -> None:
    """Раз в полминуты: пора ли делать прогон. Один раз в день, по DAILY_RUN_AT в TZ."""
    store = get_store()
    while True:
        try:
            now = dt.datetime.now(TZ)
            marker = (store.get(META, "daily") or {}).get("date")
            if now.strftime("%H:%M") == DAILY_RUN_AT and marker != now.date().isoformat():
                store.put(META, "daily", {"date": now.date().isoformat(), "started": now.isoformat()})
                await daily_job()
        except Exception:  # noqa: BLE001
            log.exception("Планировщик")
        await asyncio.sleep(30)


# ------------------------------------------------------------------ запуск

async def main() -> None:
    global _agent, _bot

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Нет TELEGRAM_BOT_TOKEN. Получите токен у @BotFather и положите его "
            "в .env рядом с docker-compose.yml."
        )
    _config.require_api_key()
    if not ALLOWED:
        log.warning("ALLOWED_USER_IDS пуст — бот никого не пустит, но покажет каждому его ID.")
    if GROUP_ID is None:
        log.warning("GROUP_ID не задан — режим личного чата, тема на клиента недоступна.")
    if not actions.google_configured():
        log.warning("Google не настроен (DRIVE_FOLDER_ID / ключ сервисного аккаунта) — книги и прогон недоступны.")

    checkpointer = build_checkpointer(_config)
    if checkpointer is None:
        log.warning("MONGODB_URI не задан — история диалогов и состояние подборов не переживут перезапуск.")
    else:
        log.info("Память сессий: MongoDB, база %s", _config.mongodb_db)
    log.info("Хранилище Scout: %s", get_store().backend)
    try:
        runner.restore_catalog(get_store())
    except Exception as error:  # noqa: BLE001
        log.warning("Каталог проектов не восстановлен: %s", error)

    _agent = build_agent(_config, checkpointer=checkpointer)

    _bot = Bot(token=token)
    me = await _bot.get_me()
    log.info("Бот @%s запущен, модель %s, прогон %s %s", me.username, _config.model, DAILY_RUN_AT, TZ.key)
    asyncio.create_task(scheduler())
    await dp.start_polling(_bot)


if __name__ == "__main__":
    asyncio.run(main())
