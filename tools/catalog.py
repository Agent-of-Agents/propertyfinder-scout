"""Инструменты доступа к каталогу недвижимости propertyfinder.ae.

Используется только стандартная библиотека: страница скачивается обычным
HTTP-запросом и очищается от разметки. Сайт отдаёт серверный HTML, поэтому
заголовки и цены промо-блоков видны без браузера.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from langchain.tools import tool

ALLOWED_HOSTS = ("www.propertyfinder.ae", "propertyfinder.ae")
# разделы, закрытые в robots.txt самого сайта — туда не ходим
DISALLOWED_PREFIXES = ("/ajax/", "/promo/", "/manager/", "/to/", "/search", "/en/search", "/ar/search")
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}
MAX_CHARS = 8000
TIMEOUT_SEC = 45


def _normalize(path_or_url: str) -> str:
    """Привести вход к полному URL и не выпустить запрос за пределы каталога."""
    url = path_or_url.strip()
    if not url.startswith("http"):
        url = "https://www.propertyfinder.ae/" + url.lstrip("/")
    parts = urllib.parse.urlsplit(url)
    if parts.netloc not in ALLOWED_HOSTS:
        raise ValueError(f"разрешён только propertyfinder.ae, получено: {parts.netloc}")
    if any(parts.path.startswith(p) for p in DISALLOWED_PREFIXES):
        raise ValueError(f"путь {parts.path} закрыт в robots.txt сайта")
    return urllib.parse.urlunsplit(parts)


def _download(url: str) -> str:
    request = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
        return response.read().decode("utf-8", "replace")


def _visible_text(raw_html: str) -> str:
    """Выбросить разметку и скрипты, оставив читаемый текст."""
    cleaned = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw_html)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


@tool
def open_catalog_page(path: str) -> str:
    """Открыть страницу каталога propertyfinder.ae и вернуть её видимый текст.

    Args:
        path: путь или полный URL на propertyfinder.ae, например "/" или
            "/en/buy/properties-for-sale.html". Разделы поиска закрыты robots.txt.

    Returns:
        Текст страницы с указанием URL и размера исходного HTML; длинные
        страницы обрезаются. При ошибке — понятное текстовое объяснение.
    """
    try:
        url = _normalize(path)
    except ValueError as error:
        return f"Отказано: {error}"
    try:
        raw = _download(url)
    except urllib.error.HTTPError as error:
        return f"Сайт ответил HTTP {error.code} ({error.reason}) на {url}"
    except Exception as error:  # сетевые сбои не должны ронять агента
        return f"Не удалось загрузить {url}: {type(error).__name__}: {error}"

    text = _visible_text(raw)
    tail = "" if len(text) <= MAX_CHARS else f"\n[...обрезано, всего {len(text)} символов]"
    return f"URL: {url}\nРазмер HTML: {len(raw)} символов\n\n{text[:MAX_CHARS]}{tail}"


@tool
def extract_listings(path: str) -> str:
    """Достать со страницы каталога структурные данные: schema.org, цены, ссылки.

    Args:
        path: путь или полный URL страницы каталога propertyfinder.ae.

    Returns:
        Найденные JSON-LD блоки, цены в AED и ссылки на разделы или карточки.
        Если структурных данных нет — прямое сообщение об этом.
    """
    try:
        url = _normalize(path)
        raw = _download(url)
    except ValueError as error:
        return f"Отказано: {error}"
    except Exception as error:
        return f"Не удалось загрузить страницу: {type(error).__name__}: {error}"

    blocks: list[str] = []
    for block in re.findall(r"(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>", raw):
        try:
            blocks.append(json.dumps(json.loads(block), ensure_ascii=False)[:1500])
        except json.JSONDecodeError:
            continue

    prices = list(dict.fromkeys(re.findall(r"AED\s?[\d,]{4,}", _visible_text(raw))))[:25]
    links = list(dict.fromkeys(re.findall(r'href="(/en/plp/[^"]+|/en/buy/[^"]+\.html)"', raw)))[:15]

    parts = [f"URL: {url}"]
    if blocks:
        parts.append("JSON-LD блоки:\n" + "\n---\n".join(blocks[:5]))
    if prices:
        parts.append("Цены на странице: " + ", ".join(prices))
    if links:
        parts.append("Ссылки на разделы и карточки:\n" + "\n".join(links))
    if len(parts) == 1:
        parts.append("Структурных данных не нашлось — контент может подгружаться скриптами.")
    return "\n\n".join(parts)
