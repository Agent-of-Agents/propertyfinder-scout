"""Тесты слоя Scout Dubai: хранилище, модель, карточки, действия без сети."""

from __future__ import annotations

import datetime as dt

import pytest

from lib import sync
from scout import actions, cards
from scout.models import MARKET_OFFPLAN, MARKET_SECONDARY, Client, Search, slugify, tag
from scout.store import RAW, STATES, Store, set_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("DRIVE_FOLDER_ID", raising=False)
    s = Store(data_dir=tmp_path / "scout")
    set_store(s)
    yield s
    set_store(None)


@pytest.fixture
def client_and_search(store):
    client = Client(slug="ivanova", name="Иванова Мария", spreadsheet_id="sheet-1", deadline="2026-10-15")
    search = Search(client="ivanova", slug="marina-shores-2br", title="Marina Shores 2BR",
                    bedrooms=["1", "2"], budget={"min": 3_000_000, "max": 4_500_000}, target="Marina Shores")
    actions.save_client(client, store)
    actions.save_search(search, store)
    return client, search


# ------------------------------------------------------------------ хранилище

def test_store_roundtrip(store):
    store.put("c", "a", {"x": 1})
    store.update("c", "a", {"y": 2})
    assert store.get("c", "a") == {"x": 1, "y": 2}
    store.put("c", "b", {"x": 2, "flag": True})
    assert [d["id"] for d in store.find("c", flag=True)] == ["b"]
    store.delete("c", "a")
    assert store.get("c", "a") is None
    assert store.backend == "files"


def test_state_handle_works_with_sync(store):
    handle = store.state_handle("ivanova/marina-shores-2br")
    assert sync.load_state(handle) == {"listings": {}}
    sync.save_state(handle, {"listings": {"PF-1": {"first_seen": "2026-09-01"}}})
    assert sync.load_state(handle)["listings"]["PF-1"]["first_seen"] == "2026-09-01"
    assert store.get(STATES, "ivanova/marina-shores-2br")["listings"]["PF-1"]


# ------------------------------------------------------------------ модель

def test_slugify_transliterates():
    assert slugify("Иванова Мария") == "ivanova-mariya"
    assert slugify("Marina Shores 2BR") == "marina-shores-2br"
    assert slugify("Off-plan Business Bay") == "off-plan-business-bay"


def test_tag_and_sync_client_shape(client_and_search):
    client, search = client_and_search
    assert tag(client, search) == "Иванова · Marina Shores 2BR"
    as_sync = search.as_sync_client(client)
    assert as_sync["sheet"] == "Marina Shores 2BR"
    assert as_sync["spreadsheet_id"] == "sheet-1"
    assert as_sync["bedrooms"] == ["1", "2"]
    assert sync.fits_client(as_sync, {"bedrooms": 2, "price": 4_600_000})      # +5 % запас
    assert not sync.fits_client(as_sync, {"bedrooms": 3, "price": 4_000_000})


def test_search_from_brief_defaults():
    taken = {"downtown-1br"}
    s = actions.search_from_brief("kim", {"market": MARKET_SECONDARY, "target": "Downtown", "bedrooms": ["1"],
                                          "budget": {"max": 2_500_000}}, taken)
    assert s.title == "Downtown 1BR"
    assert s.slug == "downtown-1br-2"                       # имя занято — суффикс
    assert s.distress_keywords == ["downtown"]
    o = actions.search_from_brief("kim", {"market": MARKET_OFFPLAN, "target": "Business Bay",
                                          "bedrooms": ["1", "2"], "budget": {"max": 3_000_000}}, set())
    assert o.title == "Off-plan Business Bay 1BR/2BR"
    assert o.offplan["communities"] == ["Business Bay"]
    assert o.offplan["budget"] == {"max": 3_000_000}
    assert o.as_offplan_brief()["sheet"] == o.title


# ------------------------------------------------------------------ карточки и кнопки

def test_callback_roundtrip_fits_telegram_limit():
    data = cards.cb(cards.ACT_APPROVE, "ivanova", "marina-shores-2br", "PF-141439708")
    assert len(data.encode("utf-8")) <= 64
    assert cards.parse_cb(data) == {"action": "ap", "client": "ivanova", "search": "marina-shores-2br",
                                    "listing": "PF-141439708"}
    assert cards.parse_cb("garbage") is None
    short = cards.parse_cb(cards.cb(cards.ACT_LAUNCH, "abc123"))
    assert short["client"] == "abc123" and short["search"] == "" and short["listing"] == ""


def test_listing_card_has_tag_and_three_buttons(client_and_search):
    client, search = client_and_search
    row = {"id": "PF-1", "bedrooms": 2, "size_m2": 105.1, "price": 3_850_000, "price_per_m2": 36_632,
           "market_delta": -12.0, "water_view": True, "agent_name": "Dhanvanthri", "agency": "Prop Plus",
           "ru_score": 3, "url": "https://www.propertyfinder.ae/en/plp/buy/x-141439708.html"}
    card = cards.listing_card(client, search, row, position="1 из 3")
    assert card.text.startswith("<code>Иванова · Marina Shores 2BR · 1 из 3</code>")
    assert "3 850 000 AED" in card.text and "-12 % к медиане" in card.text and "🇷🇺" in card.text
    labels = [b.label for b in card.buttons[0]]
    assert labels == ["✅ Одобрить", "📩 Запросить", "⏭"]
    assert card.meta == {"client": "ivanova", "search": "marina-shores-2br", "listing": "PF-1"}


def test_draft_cards(client_and_search):
    client, search = client_and_search
    draft = {"id": "d1", "client": {"name": "Ким Анастасия", "deadline": "2026-10-01"},
             "search": {"market": "secondary", "target": "JVC", "bedrooms": ["3"], "budget": {"max": 2_200_000},
                        "wishes": ["закрытый комплекс"]}, "missing": ["контакт"]}
    card = cards.client_draft_card(draft)
    assert "Ким Анастасия" in card.text and "до 2 200 000 AED" in card.text and "Не расслышал" in card.text
    assert [b.label for b in card.buttons[0]] == ["✅ Запустить", "✏️ Поправить"]
    sd = cards.search_draft_card(client, {"id": "d2", "search": {"market": "secondary", "target": "Downtown",
                                                                 "bedrooms": ["1"], "budget": {"max": 2_500_000}}}, [search])
    assert "Заменить Marina Shores 2BR" in sd.buttons[0][0].label
    assert sd.buttons[-1][0].label == "➕ Добавить параллельно"


def test_general_digest_groups_quiet_clients():
    rows = [{"client": "Иванова", "search": "Marina Shores 2BR", "icon": "🏠", "changed": True, "new": 3, "price": 2,
             "removed": 1, "distress": 1, "changed_projects": 0},
            {"client": "Гареев", "search": "Palm Villas", "icon": "🌴", "changed": False}]
    text = cards.general_digest(rows, ["Ахметов"], ["Иванова через 32 дн."]).text
    assert "🆕 3" in text and "💰 2" in text and "⚰️ 1" in text and "🔥 1" in text
    assert "Без изменений: Гареев" in text and "⏸ На паузе: Ахметов" in text


# ------------------------------------------------------------------ действия без сети

def test_google_not_configured_blocks_book_actions(store):
    assert not actions.google_configured()
    with pytest.raises(actions.NotConfigured):
        actions.create_client({"name": "Тест"})


def test_pending_cards_orders_ru_first_and_hides_skipped(store, client_and_search):
    client, search = client_and_search
    today = dt.date.today().isoformat()
    rows = [
        {"id": "PF-A", "bedrooms": 2, "price": 4_000_000, "price_per_m2": 38_000, "ru_score": 0},
        {"id": "PF-B", "bedrooms": 2, "price": 3_800_000, "price_per_m2": 36_000, "ru_score": 3},
        {"id": "PF-C", "bedrooms": 2, "price": 3_900_000, "price_per_m2": 37_000, "ru_score": 3},
        {"id": "PF-D", "bedrooms": 4, "price": 9_000_000, "price_per_m2": 40_000, "ru_score": 3},   # не по брифу
        {"id": "PF-OLD", "bedrooms": 2, "price": 3_500_000, "price_per_m2": 35_000, "ru_score": 3},
    ]
    store.put(RAW, search.key, {"rows": rows})
    store.put(STATES, search.key, {"listings": {"PF-OLD": {"first_seen": "2026-01-01"},
                                                "PF-A": {"first_seen": today}},
                                   "last_report": {"medians": {"2": 37_000}}})
    actions.skip_listing(search, "PF-C", store)
    pending = actions.pending_cards(client, search, store)
    assert [r["id"] for r in pending] == ["PF-B", "PF-A"]
    actions.mark_shown(search, ["PF-B"], store)
    assert [r["id"] for r in actions.pending_cards(client, search, store)] == ["PF-A"]


def test_drafts_and_pending(store):
    draft = actions.save_draft("client", {"client": {"name": "X"}, "search": {}}, store)
    assert actions.get_draft(draft["id"], store)["client"]["name"] == "X"
    actions.drop_draft(draft["id"], store)
    with pytest.raises(actions.NotFound):
        actions.get_draft(draft["id"], store)
    actions.set_pending("1|0", {"kind": "price", "client": "a", "search": "b", "listing": "PF-1"}, store)
    assert actions.pop_pending("1|0", store)["listing"] == "PF-1"
    assert actions.pop_pending("1|0", store) is None


def test_find_client_by_surname(store, client_and_search):
    client, _ = client_and_search
    assert actions.find_client("Иванова", store).slug == "ivanova"
    assert actions.find_client("ivanova", store).slug == "ivanova"
    assert actions.find_client("Петров", store) is None


def test_close_search_archives_client_without_google(store, client_and_search):
    client, search = client_and_search
    client.spreadsheet_id = ""                      # без Google лист не переименовываем, статусы — да
    actions.save_client(client, store)
    actions.close_search(client, search, "bought", store)
    assert actions.get_search("ivanova", "marina-shores-2br", store).status == "closed"
    assert actions.get_client("ivanova", store).status == "archived"


# ------------------------------------------------------------------ bot.py: чистые функции

def test_bot_helpers(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS", "22:00-08:00")
    import importlib

    import bot
    importlib.reload(bot)
    tz = bot.TZ
    assert bot.in_quiet_hours(dt.datetime(2026, 9, 17, 23, 30, tzinfo=tz))
    assert bot.in_quiet_hours(dt.datetime(2026, 9, 17, 7, 59, tzinfo=tz))
    assert not bot.in_quiet_hours(dt.datetime(2026, 9, 17, 12, 0, tzinfo=tz))
    head, tail = bot._split_caption("a" * 900 + "\n" + "b" * 500)
    assert head == "a" * 900 and tail == "b" * 500
    spec = cards.listing_card(Client("x", "X Y"), Search(client="x", slug="s", title="S"), {"id": "PF-1"})
    markup = bot.keyboard(spec)
    assert len(markup.inline_keyboard[0]) == 3
    assert markup.inline_keyboard[0][0].callback_data == "a|ap|x|s|PF-1"


# ------------------------------------------------------------------ голос

def test_transcribe_engine_detection(monkeypatch):
    from scout import transcribe

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WHISPER_LOCAL_MODEL", raising=False)
    assert transcribe.available() == ""
    with pytest.raises(transcribe.TranscribeError):
        transcribe.transcribe(__import__("pathlib").Path("nope.oga"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert transcribe.available() == "openai"


def test_multipart_body_has_fields_and_file():
    from scout import transcribe

    body, ctype = transcribe._multipart({"model": "whisper-1", "language": "ru"}, "file", "voice.ogg",
                                        "audio/ogg", b"OggS\x00data")
    assert ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=")[1].encode()
    assert body.count(b"--" + boundary) == 4            # два поля + файл + закрывающий
    assert b'name="model"\r\n\r\nwhisper-1' in body
    assert b'filename="voice.ogg"' in body and b"OggS\x00data" in body
    assert body.endswith(b"--" + boundary + b"--\r\n")


def test_budget_floor_is_enforced():
    villa_search = {"bedrooms": ["4", "5", "6"], "budget": {"min": 50_000_000, "max": 120_000_000}}
    assert not sync.fits_client(villa_search, {"bedrooms": 5, "price": 8_000_000})       # вилла за 8 млн — не тот класс
    assert sync.fits_client(villa_search, {"bedrooms": 5, "price": 46_000_000})          # −8 % к нижней — ещё показываем
    assert not sync.fits_client(villa_search, {"bedrooms": 5, "price": 40_000_000})      # −20 % — уже нет
    assert sync.fits_client(villa_search, {"bedrooms": 6, "price": 125_000_000})         # +4 % к потолку — показываем
    assert not sync.fits_client(villa_search, {"bedrooms": 6, "price": 130_000_000})
    assert sync.fits_client({"bedrooms": ["2"], "budget": {"max": 4_500_000}}, {"bedrooms": 2, "price": 1_900_000})  # без min — как раньше


def test_client_fate_pause_resume_close_delete(store, client_and_search):
    client, search = client_and_search
    client.spreadsheet_id = ""                              # без Google — только статусы
    actions.save_client(client, store)
    assert actions.pause_client(client, store) == 1
    assert actions.get_search("ivanova", "marina-shores-2br", store).status == "paused"
    assert actions.resume_client(client, store) == 1
    assert actions.get_search("ivanova", "marina-shores-2br", store).status == "active"
    closed = actions.close_client(client, "bought", store)
    assert [s.close_reason for s in closed] == ["bought"]
    assert actions.get_client("ivanova", store).status == "archived"
    # новый подбор возвращает клиента в работу — без Google create_search не вызвать, проверяем правило напрямую
    result = actions.delete_client(actions.get_client("ivanova", store), store)
    assert result["searches"] == 1 and result["book_trashed"] is False
    with pytest.raises(actions.NotFound):
        actions.get_client("ivanova", store)
    assert store.get(RAW, search.key) is None and store.get(STATES, search.key) is None


def test_fate_and_delete_cards(client_and_search):
    client, search = client_and_search
    card = cards.client_fate_card(client, [search], note="купил")
    labels = [b.label for row in card.buttons for b in row]
    assert labels[:4] == ["⏸ Заморозить", "✓ Куплено", "✕ В архив", "🗑 Удалить полностью"]
    assert "купил" in card.text
    confirm = cards.delete_confirm_card(client, [search])
    assert confirm.buttons[0][0].data == cards.cb(cards.ACT_DELETE_CONFIRM, "ivanova")


def test_find_drafts_by_name(store):
    d = actions.save_draft("client", {"client": {"name": "Хилтон Пэрис"}, "search": {}}, store)
    assert [x["id"] for x in actions.find_drafts("хилтон", store)] == [d["id"]]
    assert actions.find_drafts("иванов", store) == []
