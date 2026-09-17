"""Распознавание голосовых: Whisper.

Основной путь — API OpenAI (`OPENAI_API_KEY`): сервер не грузим, качество лучшее,
цена — копейки за минуту. Запасной — локальный faster-whisper, если установлен
пакет и задан `WHISPER_LOCAL_MODEL`; на CPU дроплета это медленно и тяжело,
поэтому по умолчанию выключен.

Telegram отдаёт голосовые как OGG/Opus (`.oga`), аудио — mp3/m4a, кружки — mp4;
Whisper понимает всё это без перекодирования.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

log = logging.getLogger("scout.transcribe")

OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_MODEL = "whisper-1"
TIMEOUT = 120
# Подсказка модели: в брифах русская речь с английскими названиями проектов и районов.
PROMPT = ("Бриф брокера по недвижимости Дубая: Marina Shores, Dubai Marina, Downtown, Business Bay, "
          "JVC, Palm Jumeirah, Emaar, Binghatti, off-plan, 1BR, 2BR, AED, млн дирхам.")


class TranscribeError(RuntimeError):
    """Распознать не вышло — причина в тексте."""


def available() -> str:
    """Какой движок доступен: «openai», «local» или «» (ни один)."""
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("WHISPER_LOCAL_MODEL"):
        try:
            import faster_whisper  # noqa: F401
            return "local"
        except ImportError:
            return ""
    return ""


def transcribe(path: Path, language: str = "ru") -> str:
    """Текст из аудиофайла. Пустая строка — если в записи нет речи."""
    engine = available()
    if engine == "openai":
        return _openai(path, language)
    if engine == "local":
        return _local(path, language)
    raise TranscribeError(
        "Распознавание голоса не настроено: задай OPENAI_API_KEY в .env "
        "(или WHISPER_LOCAL_MODEL и пакет faster-whisper для локального Whisper)."
    )


def _openai(path: Path, language: str) -> str:
    key = os.environ["OPENAI_API_KEY"]
    model = os.environ.get("WHISPER_MODEL", DEFAULT_MODEL)
    filename = path.name if path.suffix.lower() not in (".oga", "") else path.stem + ".ogg"
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    fields = {"model": model, "language": language, "prompt": PROMPT, "response_format": "json",
              "temperature": "0"}
    body, content_type = _multipart(fields, "file", filename, mime, path.read_bytes())
    request = urllib.request.Request(
        OPENAI_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        if error.code == 401:
            raise TranscribeError("OpenAI не принял ключ (401) — проверь OPENAI_API_KEY") from error
        raise TranscribeError(f"OpenAI HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise TranscribeError(f"OpenAI недоступен: {error.reason}") from error
    return (answer.get("text") or "").strip()


def _local(path: Path, language: str) -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(os.environ["WHISPER_LOCAL_MODEL"], device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(path), language=language, initial_prompt=PROMPT, vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()


def _multipart(fields: dict[str, str], file_field: str, filename: str, mime: str,
               data: bytes) -> tuple[bytes, str]:
    """multipart/form-data руками — чтобы не тащить лишнюю зависимость ради одного запроса."""
    boundary = f"----scout{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {mime}\r\n\r\n".encode("utf-8") + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
