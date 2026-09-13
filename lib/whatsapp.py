"""Запросы брокерам через WhatsApp-шлюз Property Finder.

Property Finder не отдаёт личный номер агента. Кнопка WhatsApp в объявлении
ведёт на их общий шлюз (+971 4 556 0345) с предзаполненным текстом.

Как это работает на самом деле — выяснено 11.09.2026 после того, как 15
запросов ушли впустую:

  * кнопка на сайте — не ссылка, а запрос к серверу PF:
        POST /leads/v1/lead-request/whatsapp  {entity_id, agent_id, client_id}
    сервер регистрирует «попытку контакта» и отвечает ссылкой, в тексте
    которой есть уникальный attempt_id;
  * бот PF в WhatsApp узнаёт запрос именно по attempt_id. Старый текст
    «Hello, I would like to get more information…», который до сих пор лежит
    в contact_options разметки, бот отвергает: «enquiry didn't go through».

Поэтому ссылку для каждого объекта надо запрашивать у PF, а не брать со
страницы. Текст по-прежнему нельзя менять — «(do not edit link)».

Отправку выполняет человек: ссылка открывает WhatsApp с готовым сообщением,
остаётся нажать «отправить». Неиспользованная ссылка ничего не отправляет.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .propertyfinder import NEXT_DATA, UA, fetch

LEAD_ENDPOINT = "https://www.propertyfinder.ae/leads/v1/lead-request/whatsapp"

# Признак живой ссылки: без attempt_id бот запрос не примет
ATTEMPT_MARK = "attempt_id"


class LeadRequestError(RuntimeError):
    """PF не выдал ссылку — причина в тексте ошибки."""


def listing_identity(listing_url: str) -> dict:
    """Идентификаторы объявления, нужные для запроса ссылки."""
    match = NEXT_DATA.search(fetch(listing_url))
    if not match:
        raise LeadRequestError("на странице нет данных объявления")

    page = json.loads(match.group(1))["props"]["pageProps"]
    prop = (page.get("propertyResult") or {}).get("property") or {}
    if not prop.get("listing_id"):
        raise LeadRequestError("в данных объявления нет listing_id")

    return {
        "listing_id": prop["listing_id"],
        "agent_id": str((prop.get("agent") or {}).get("id") or ""),
        "client_id": str((prop.get("broker") or {}).get("id") or ""),
        # Для страницы-помощника: в новом тексте PF этого уже нет
        "reference": prop.get("reference") or "",
        "building": (prop.get("location") or {}).get("name") or "",
    }


def request_link(identity: dict, listing_url: str) -> dict:
    """Попросить у PF ссылку с attempt_id — то же, что тап по кнопке на сайте.

    Возвращает {"link", "phone", "text"}.
    """
    body = json.dumps({
        # Так сейчас отправляет сам сайт: капча у них выключена
        "captcha_token": "RECAPTCHA_DISABLED_PLACEHOLDER_TOKEN",
        "click_id": str(uuid.uuid4()),
        "entity": "listing",
        "entity_id": identity["listing_id"],
        "language": "en",
        "metadata": {
            "agent_id": identity["agent_id"],
            "client_id": identity["client_id"],
        },
    }).encode("utf-8")

    request = urllib.request.Request(
        LEAD_ENDPOINT, data=body, method="POST",
        headers={
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://www.propertyfinder.ae",
            "Referer": listing_url,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:200]
        if error.code in (403, 405, 429):
            raise LeadRequestError(
                f"PF не пустил скрипт (HTTP {error.code}) — вероятно, защита WAF. "
                f"Ссылку придётся брать через браузер. {detail}"
            ) from error
        raise LeadRequestError(f"HTTP {error.code}: {detail}") from error

    link = answer.get("whatsapp_link") or ""
    if not link:
        raise LeadRequestError(f"в ответе нет whatsapp_link: {json.dumps(answer)[:200]}")

    return link_parts(link)


def looks_authentic(text: str) -> bool:
    """Есть ли в сообщении attempt_id — без него бот PF запрос отвергнет."""
    return ATTEMPT_MARK in text


def link_parts(link: str) -> dict:
    """Разобрать ссылку api.whatsapp.com на номер и текст."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    return {
        "link": link,
        "phone": (query.get("phone") or [""])[0],
        "text": (query.get("text") or [""])[0],
    }


def collect_links(listings: list[dict], pause: float = 1.5,
                  links: dict[str, str] | None = None) -> list[dict]:
    """Собрать ссылки для списка объявлений.

    Каждый элемент — словарь строки таблицы, обязательны ключи id и url.
    К нему добавляются wa_link, wa_text, wa_error.

    links — готовые ссылки {id: whatsapp_link}, полученные через браузер:
    прямой запрос к PF из скрипта режет WAF (HTTP 403), а браузер проходит.
    """
    result = []
    for listing in listings:
        item = dict(listing)
        item.update({"wa_link": "", "wa_text": "", "wa_error": ""})
        url = item.get("url") or ""
        if not url:
            item["wa_error"] = "нет ссылки на объявление"
            result.append(item)
            continue

        try:
            identity = listing_identity(url)
            if links is not None:
                ready = links.get(item.get("id") or "")
                if not ready:
                    raise LeadRequestError("браузер не вернул ссылку для этого объекта")
                found = link_parts(ready)
            else:
                found = request_link(identity, url)
        except LeadRequestError as error:
            item["wa_error"] = str(error)
            result.append(item)
            time.sleep(pause)
            continue
        except Exception as error:  # noqa: BLE001
            item["wa_error"] = f"страница не открылась: {error}"
            result.append(item)
            continue

        item["wa_link"] = found["link"]
        item["wa_text"] = found["text"]
        item.setdefault("reference", identity["reference"])
        item.setdefault("building", identity["building"])
        if not looks_authentic(found["text"]):
            # PF снова поменял формат — отправлять можно, но пусть Алексей глянет
            item["wa_error"] = "в ссылке нет attempt_id — проверьте ответ бота после отправки"

        result.append(item)
        time.sleep(pause)

    return result


TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "whatsapp_page.html"

# --------------------------------------------------------- прямой канал

# Шлюз PF (+971 4 556 0345) принимает сообщение только после проверки на их
# сайте — лид-запрос с капчей, залогиненный пользователь. Открыть WhatsApp по
# готовой ссылке, минуя сайт, нельзя: шлюз отбивает «enquiry didn't go through».
# Проверено 11.09.2026, текст был байт в байт тот же. Обходить капчу не будем.
#
# Поэтому основной канал — мобильный номер агентства из объявления
# (broker.phone). Это опубликованный рабочий номер, туда пишем своим текстом.

UAE_MOBILE = re.compile(r"^\+9715\d{8}$")

DIRECT_TEXT = (
    "Hello! I'm a broker with a cash buyer for your listing in {building}"
    "{ref_part} — {price} AED.\n"
    "{url}\n\n"
    "Is it still available? Could you share the floor plan and your best price "
    "for a quick cash deal? Thank you."
)


def _clean_phone(phone: str) -> str:
    """971505259576 / 0505259576 / +971 50 … → +971505259576."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        digits = "971" + digits[1:]
    return f"+{digits}" if digits else ""


def is_mobile(phone: str) -> bool:
    return bool(UAE_MOBILE.match(_clean_phone(phone)))


def direct_link(phone: str, item: dict) -> str:
    """wa.me-ссылка на мобильный агентства с нашим текстом. Пусто — если номер офисный."""
    clean = _clean_phone(phone)
    if not is_mobile(clean):
        return ""
    price = item.get("price")
    price_text = f"{int(price):,}".replace(",", " ") if isinstance(price, (int, float)) else str(price or "")
    ref = item.get("reference") or ""
    text = DIRECT_TEXT.format(
        building=item.get("building") or "Dubai Marina",
        ref_part=f" (ref. {ref})" if ref else "",
        price=price_text,
        url=item.get("url") or "",
    )
    return f"https://wa.me/{clean.lstrip('+')}?text={urllib.parse.quote(text)}"


def build_page(items: list[dict], batch: str, built: str = "") -> str:
    """Страница со списком запросов: кнопка на объект, отметки в localStorage.

    batch — идентификатор подборки, по нему разделяются отметки об отправке,
    чтобы новая подборка не показывалась уже отправленной.
    """
    payload = {
        "batch": batch,
        "built": built,
        "items": [
            {
                "id": item.get("id"),
                "price": item.get("price"),
                "delta": item.get("delta") or "",
                "rooms": item.get("rooms") or "",
                "size_m2": item.get("size_m2") or "",
                "building": item.get("building") or "",
                "agent": item.get("agent") or "",
                "agency": item.get("agency") or "",
                "reference": item.get("reference") or "",
                "url": item.get("url") or "",
                "wa_link": item.get("wa_link") or "",
                "wa_text": item.get("wa_text") or "",
                "problem": item.get("wa_error") or "",
                # Личный мобильный агента из карты DLD надёжнее номера агентства
                "phone": item.get("dld_phone") or item.get("agency_phone") or "",
                "phone_source": "DLD, личный" if item.get("dld_phone") else "агентство",
                "direct_link": direct_link(item.get("dld_phone") or item.get("agency_phone") or "", item),
                "ru": bool(item.get("ru")),
            }
            for item in items
        ],
    }

    # Внутри <script type="application/json"> нельзя оставлять закрывающий тег
    data = json.dumps(payload, ensure_ascii=False).replace("</", r"<\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", data)
