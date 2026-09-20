"""DLD Trakheesi через Telegram: бот готовит ссылки — Алексей открывает — бот записывает.

Карта разрешения DLD (за QR-кодом объявления) даёт то, чего нет на PF: точную площадь
до сотых, ЛИЧНЫЙ мобильный и email брокера, срок разрешения. Карта защищена
reCAPTCHA v3 — читается только настоящим браузером человека; капчу не обходим.
Поэтому:

  1. `queue()` — по подбору собираем ссылки на карты (permit_validation_url с карточки
     объявления PF, через прокси) и шлём в тему по одной: «🪪 Брокер · цена · [Открыть карту]».
  2. Алексей открывает ссылку (телефон/ПК), капча проходит на нём, и отвечает на то же
     сообщение скриншотом карты или вставляет её текст.
  3. `ingest_text()` / `extract_from_image()` → `apply_card()`: поля в лист по названиям
     колонок, дубли ✔ по точной площади, ответ «Записано».

Карты хранятся в MongoDB (scout_dld_cards), чтобы дубли считались по всем накопленным.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import re

from lib import listing_detail, trakheesi

from . import actions
from .models import MARKET_SECONDARY, Client, Search
from .store import Store, get_store

log = logging.getLogger("scout.dld")

CARDS = "scout_dld_cards"          # id: "<client>/<search>/<listing_id>"
DLD_COLUMNS = ["DLD м²", "Тел. брокера DLD", "Email DLD", "Разрешение до", "Юнит DLD", "Дубли"]

VISION_PROMPT = """На изображении — карта разрешения DLD «Real Estate Permit Card» (Дубай) или похожий документ.
Извлеки поля и верни ТОЛЬКО JSON без пояснений, ключи:
permit_number (Transaction Number), permit_until (End Date, как на карте), license (License #),
authority (Authority Name), broker_name (Broker Name), broker_email, broker_mobile,
building (Building Name), property_name, size_sqm (Property Size Sqm, число), value_aed (число),
rooms, zone. Отсутствующее поле — null. Если это не карта DLD — верни {"verified": false}."""


def _key(client: Client, search: Search, listing_id: str) -> str:
    return f"{client.slug}/{search.slug}/{listing_id}"


# ------------------------------------------------------------------ очередь

def queue(client: Client, search: Search, limit: int = 10, store: Store | None = None) -> list[dict]:
    """Объекты, которым нужна карта DLD: одобренные и запрошенные — первыми, потом по порядку листа.

    Пропускаем строки, где карта уже есть. Ссылку на карту берём с карточки объявления
    (через прокси). Возвращает [{listing_id, agent, price, url, permit_url}].
    """
    store = store or get_store()
    if search.market != MARKET_SECONDARY:
        return []
    view = actions.SheetRows(client, search)
    known = {d["listing"] for d in store.find(CARDS, client=client.slug, search=search.slug)}
    raw = {r["id"]: r for r in actions.raw_rows(search, store)}

    def truthy(v: str) -> bool:
        return str(v).strip().upper() == "TRUE"

    ranked = []
    for offset, row in enumerate(view.rows):
        lid = view.cell(row, "listing_id")
        if not lid or lid in known or view.cell(row, "DLD м²"):
            continue
        priority = 0 if (truthy(view.cell(row, "✅ Одобрено")) or truthy(view.cell(row, "📩 Запросить"))
                         or view.cell(row, "Запрошено")) else 1
        ranked.append((priority, offset, lid, row))
    ranked.sort(key=lambda x: (x[0], x[1]))

    out = []
    for _, _, lid, row in ranked[:limit]:
        r = raw.get(lid, {})
        url = r.get("url") or view.cell(row, "Ссылка")
        permit_url = ""
        try:
            detail = listing_detail.detail_or_row({**r, "url": url})
            permit_url = detail.get("permit_validation_url") or ""
        except Exception as error:  # noqa: BLE001
            log.info("DLD-ссылка для %s не получена: %s", lid, error)
        out.append({"listing_id": lid, "agent": r.get("agent_name") or view.cell(row, "Брокер"),
                    "agency": r.get("agency") or view.cell(row, "Агентство"),
                    "price": r.get("price") or view.cell(row, "Цена AED"), "url": url, "permit_url": permit_url,
                    "building": r.get("building") or "", "size_m2": r.get("size_m2")})
    return out


# ------------------------------------------------------------------ разбор

def ingest_text(text: str) -> dict:
    """Текст страницы Trakheesi → поля карты. {} — это не карта."""
    return trakheesi.parse_card(text)


def extract_from_image(image: bytes, model: str, mime: str = "image/jpeg") -> dict:
    """Скриншот карты DLD → поля. Читает модель агента (она понимает изображения)."""
    from langchain.chat_models import init_chat_model
    from langchain_core.messages import HumanMessage

    llm = init_chat_model(model, temperature=0)
    b64 = base64.b64encode(image).decode("ascii")
    message = HumanMessage(content=[
        {"type": "text", "text": VISION_PROMPT},
        {"type": "image", "source_type": "base64", "mime_type": mime, "data": b64},
    ])
    answer = llm.invoke([message])
    text = answer.content if isinstance(answer.content, str) else "".join(
        c.get("text", "") for c in answer.content if isinstance(c, dict))
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        card = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    if card.get("verified") is False:
        return {"verified": False}
    card = {k: v for k, v in card.items() if v not in (None, "", "null")}
    card["verified"] = True
    if card.get("size_sqm") is not None:
        try:
            card["size_sqm"] = float(str(card["size_sqm"]).replace(",", ""))
        except ValueError:
            card.pop("size_sqm", None)
    if card.get("broker_mobile"):
        card["broker_mobile"] = trakheesi.normalize_phone(str(card["broker_mobile"]))
    return card


# ------------------------------------------------------------------ сопоставление и запись

def match_listing(card: dict, candidates: list[dict]) -> str | None:
    """К какому объекту очереди относится карта: по брокеру, потом по зданию и площади."""
    broker = (card.get("broker_name") or "").strip().lower()
    building = (card.get("building") or "").strip().lower()
    size = card.get("size_sqm")
    scored = []
    for c in candidates:
        score = 0
        agent = (c.get("agent") or "").strip().lower()
        if broker and agent and (broker in agent or agent in broker or broker.split()[0] in agent):
            score += 2
        if building and c.get("building") and building.split()[0] in str(c["building"]).lower():
            score += 1
        if size and c.get("size_m2") and abs(float(c["size_m2"]) - float(size)) <= 1.5:
            score += 1
        if score:
            scored.append((score, c["listing_id"]))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None                                    # двое одинаково похожи — спросим
    return scored[0][1]


def apply_card(client: Client, search: Search, listing_id: str, card: dict,
               store: Store | None = None) -> dict:
    """Карта → колонки DLD в листе, дубли ✔ по точной площади. Возвращает что записано."""
    store = store or get_store()
    if not card.get("verified"):
        raise ValueError("Это не карта DLD или объявление в DLD не подтверждено")
    card = dict(card)
    card.update({"ingested": dt.datetime.now().isoformat(), "client": client.slug,
                 "search": search.slug, "listing": listing_id})
    store.put(CARDS, _key(client, search, listing_id), card)

    # дубли — по всем накопленным картам этого подбора
    all_cards = {d["listing"]: d for d in store.find(CARDS, client=client.slug, search=search.slug)}
    dupes = trakheesi.find_duplicates(all_cards)
    view = actions.SheetRows(client, search)
    raw = {r["id"]: r for r in actions.raw_rows(search, store)}

    def money(v) -> str:
        try:
            return f"{int(v):,}".replace(",", " ")
        except (TypeError, ValueError):
            return str(v or "")

    def dupes_text(lid: str) -> str:
        lines = []
        for other in dupes.get(lid, []):
            r = raw.get(other, {})
            # одинаковая площадь до сотых в том же здании — та же квартира (зеркальные серии — см. память)
            lines.append(f"✔ {money(r.get('price'))} AED · {r.get('agent_name') or ''} · {r.get('url') or ''}".strip(" ·"))
        return "\n".join(lines)

    written = {}
    for lid in {listing_id, *dupes.get(listing_id, [])}:
        c = all_cards.get(lid, {})
        values = {
            "DLD м²": c.get("size_sqm") or "",
            "Тел. брокера DLD": c.get("broker_mobile") or "",
            "Email DLD": c.get("broker_email") or "",
            "Разрешение до": c.get("permit_until") or "",
            "Юнит DLD": "реестра здания нет" if c.get("size_sqm") else "",
        }
        text = dupes_text(lid)
        if text:
            values["Дубли"] = text
        try:
            view.write(lid, values)
            written[lid] = values
        except actions.NotFound:
            continue
    return {"listing": listing_id, "card": card, "written": written, "dupes": dupes.get(listing_id, [])}


def summary_line(card: dict) -> str:
    bits = []
    if card.get("size_sqm"):
        bits.append(f"{card['size_sqm']:.2f} м²")
    if card.get("broker_mobile"):
        bits.append(card["broker_mobile"])
    if card.get("broker_email"):
        bits.append(card["broker_email"])
    if card.get("permit_until"):
        bits.append(f"разрешение до {card['permit_until']}")
    return " · ".join(bits) or "поля не распознаны"
