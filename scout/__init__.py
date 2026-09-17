"""Scout Dubai — прикладной слой агента.

lib/   — предметная логика (Property Finder, Google, сверка, PDF), без знания о боте.
scout/ — модель «клиент → подбор → объект», хранилище, книги, карточки, действия,
         дневной прогон. Не знает о LangChain и aiogram.
tools/ — LangChain-обёртки над scout.actions для агента.
bot.py — Telegram: темы, кнопки, планировщик; вызывает scout.actions напрямую.
"""
