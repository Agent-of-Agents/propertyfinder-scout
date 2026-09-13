"""ТУПИКОВЫЙ ПУТЬ — не использовать. Оставлено, чтобы не пробовать заново.

Задача была: приглушить водяной знак агентства на фото объявления.
Идея: знак стоит в одном месте на всех кадрах объявления, значит по стопке
кадров его можно вычислить как постоянную составляющую и частично вычесть.

Почему не работает. Кадров в объявлении 10–15, и все они — разные комнаты.
Медиана по такой стопке не сходится к ровному фону, в ней остаётся структура
чужих кадров. Вычитание даёт разводы по всему фото, а сам знак почти не слабеет.
Проверено 2026-09-09 на двух объявлениях: результат заметно хуже оригинала.

Как детектор тоже негоден: чистые рендеры застройщика получили оценку выше
(max 91.5), чем фото со слабым знаком (78.0) — метрика меряет структуру
изображения, а не знак.

Надёжное решение той же задачи одно: брать фото из чистого источника.
Для офф-плана это рендеры застройщика из assets/projects/ — там знаков нет.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

MIN_FRAMES = 5
DEFAULT_STRENGTH = 0.65     # какую долю осветления снимаем; знак остаётся виден


def _stack(paths: list[Path], size: tuple[int, int]) -> np.ndarray | None:
    frames = []
    for path in paths:
        try:
            image = Image.open(path).convert("RGB").resize(size, Image.LANCZOS)
        except Exception:  # noqa: BLE001
            continue
        frames.append(np.asarray(image, dtype=np.float32))
    if len(frames) < MIN_FRAMES:
        return None
    return np.stack(frames)


def estimate_overlay(paths: list[Path], size: tuple[int, int]) -> np.ndarray | None:
    """Карта осветления, внесённого водяным знаком. None — если не определить."""
    stack = _stack(paths, size)
    if stack is None:
        return None

    median = np.median(stack, axis=0)

    # Фон знака — та же медиана, но сильно размытая: знак в ней растворяется.
    blurred = np.asarray(
        Image.fromarray(median.astype(np.uint8)).filter(ImageFilter.GaussianBlur(28)),
        dtype=np.float32,
    )

    overlay = np.clip(median - blurred, 0, None)

    # Отсекаем шум: реальный знак даёт заметное и согласованное осветление
    strength = overlay.mean(axis=2, keepdims=True)
    overlay = np.where(strength > 3.0, overlay, 0.0)

    if float(overlay.max()) < 6.0:
        return None                      # знака нет либо он неотличим от фона
    return overlay


def dim_watermarks(paths: list[Path], target: Path,
                   strength: float = DEFAULT_STRENGTH) -> list[Path]:
    """Сохранить копии фото с приглушённым знаком. Возвращает новые пути.

    Если знак определить не удалось, фото копируются как есть.
    """
    if not paths:
        return []

    target.mkdir(parents=True, exist_ok=True)
    with Image.open(paths[0]) as first:
        size = first.size

    overlay = estimate_overlay(paths, size)
    result: list[Path] = []

    for path in paths:
        output = target / path.name
        try:
            image = Image.open(path).convert("RGB")
        except Exception:  # noqa: BLE001
            continue

        if overlay is None:
            image.save(output, quality=92)
        else:
            original_size = image.size
            array = np.asarray(image.resize(size, Image.LANCZOS), dtype=np.float32)
            cleaned = np.clip(array - overlay * strength, 0, 255).astype(np.uint8)
            Image.fromarray(cleaned).resize(original_size, Image.LANCZOS).save(
                output, quality=92
            )

        result.append(output)

    return result
