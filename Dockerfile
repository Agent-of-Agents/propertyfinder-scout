FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Код агента пишет языковая модель — пусть работает без прав root.
RUN useradd --create-home --uid 10001 agent && chown -R agent:agent /app
USER agent

# Портов не открываем: бот сам ходит в Telegram за сообщениями.
CMD ["python", "bot.py"]
