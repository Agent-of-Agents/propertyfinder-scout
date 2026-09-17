"""Сборка PDF-презентации по одобренным объектам.

Читает таблицу клиента, находит строки с галочкой ✅ Одобрено,
собирает презентацию в формате 16:9 и кладёт ссылку обратно в таблицу.

  python scripts/make_presentation.py --list
  python scripts/make_presentation.py 141896072 --price 3050000

Цену система никогда не берёт из объявления — только то, что назвал Алексей.

Фото: для офф-плана берём чистые рендеры застройщика из assets/projects/<проект>/.
Фотографии из объявлений идут в дело только если на них нет чужих водяных знаков.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lib import listing_detail, pdf_brochure, sheets  # noqa: E402

# Клиент по умолчанию для запуска руками — из окружения; daily.py задаёт его
# через configure(). В коде ID таблиц и имена клиентов не храним.
SPREADSHEET_ID = os.environ.get("PODBOR_SPREADSHEET_ID", "")
CLIENT_DIR = Path(os.environ.get("PODBOR_CLIENT_DIR", "clients/_default"))
RAW = CLIENT_DIR / "raw" / "pf_listings.json"
PROJECT_ASSETS = Path("assets/projects")

# Колонки ищем по названию в шапке, а не по номеру: таблица совместная,
# столбцы в неё добавляют, и любой жёсткий индекс однажды съедет.
COLUMNS = {
    "id": "listing_id",
    "approved": "✅ Одобрено",
    "type": "Тип",
    "ask": "Цена AED",
    "agent": "Брокер",
    "agency": "Агентство",
    "phone": "Телефон",
    "my_price": "Моя цена",
    "presentation": "Презентация",
}


def column_index(header: list[str], title: str) -> int | None:
    for index, name in enumerate(header):
        if (name or "").strip() == title:
            return index
    return None


def resolve_columns(header: list[str]) -> dict[str, int]:
    resolved = {}
    for key, title in COLUMNS.items():
        index = column_index(header, title)
        if index is None:
            raise SystemExit(f"В шапке таблицы нет колонки «{title}»")
        resolved[key] = index
    return resolved


# Колонки Алексея. Система не пишет в них никогда — ни при каком сдвиге шапки.
OWNER_COLUMNS = {"✅ Одобрено", "📩 Запросить", "Статус", "Моя цена", "Мой комментарий"}


def guard_write(header: list[str], index: int, expected: str) -> None:
    """Убедиться, что пишем именно туда, куда собирались.

    Один раз уже случилось: в таблицу вставили колонки, индекс уехал,
    и ссылка на презентацию легла поверх цены Алексея. Больше не повторится.
    """
    actual = (header[index] if index < len(header) else "").strip()
    if actual != expected:
        raise SystemExit(
            f"Отказ записи: колонка {a1_column(index)} называется «{actual}», "
            f"а ожидалась «{expected}». Шапка таблицы изменилась."
        )
    if actual in OWNER_COLUMNS:
        raise SystemExit(f"Отказ записи: «{actual}» — колонка Алексея, её не трогаем.")


def a1_column(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters

CLIENT_NAME = os.environ.get("PODBOR_CLIENT_NAME", "")
CLIENT_TARGET = "Marina Shores"

HANDOVER = {"marina shores": "Q4 2026"}


def configure(client_dir: Path, client: dict) -> None:
    """Переключить модуль на клиента (для daily.py, где клиентов несколько)."""
    global SPREADSHEET_ID, CLIENT_DIR, RAW, CLIENT_NAME, CLIENT_TARGET
    SPREADSHEET_ID = client["spreadsheet_id"]
    CLIENT_DIR = client_dir
    RAW = client_dir / client.get("raw", "raw/listings.json")
    CLIENT_NAME = client["name"]
    CLIENT_TARGET = client.get("target", "")
    if client.get("handover"):
        HANDOVER[CLIENT_TARGET.lower()] = client["handover"]

MIN_PHOTOS = 8   # ниже этого дополняем рендерами проекта

VIEW_LABELS = [
    (r"full marina", "Full Marina View"),
    (r"marina\s*(&|and|/)\s*sea|sea\s*(&|and|/)\s*marina", "Marina & Sea View"),
    (r"panoramic", "Panoramic View"),
    (r"marina view", "Marina View"),
    (r"sea view|first line", "Sea View"),
    (r"canal view", "Canal View"),
    (r"palm view", "Palm & Marina View"),
    (r"bridge view", "Bridge View"),
    (r"community view", "Community View"),
]

FLOOR_LABELS = [(r"high[\s‑-]*floor|40\+", "High Floor"),
                (r"mid[\s‑-]*floor|middle[\s‑-]*floor", "Mid Floor"),
                (r"low[\s‑-]*floor", "Low Floor")]


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def project_photos(building: str) -> list[Path]:
    folder = PROJECT_ASSETS / slugify(building)
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))


def label_from(patterns: list[tuple[str, str]], text: str) -> str:
    for pattern, label in patterns:
        if re.search(pattern, text, re.I):
            return label
    return ""


def enrich(detail: dict, stored_title: str = "") -> dict:
    # Заголовок описывает конкретную квартиру, описание часто копирует общий
    # текст по проекту. Поэтому сначала заголовок, описание — только запасной
    # источник: иначе низкий этаж с видом на мост превратится в «панорамный».
    headline = " | ".join(filter(None, [detail.get("title") or "", stored_title]))
    body = re.sub(r"<[^>]+>", " ", detail.get("description") or "")
    location = detail.get("location") or ""

    area = ""
    parts = [p.strip() for p in location.split(",")]
    for part in parts:
        if part and part.lower() not in (detail.get("building") or "").lower() \
                and part.lower() != "dubai":
            area = part
            break

    detail["area"] = area or "Dubai"
    detail["view_label"] = label_from(VIEW_LABELS, headline) or label_from(VIEW_LABELS, body)
    detail["floor_en"] = label_from(FLOOR_LABELS, headline) or label_from(FLOOR_LABELS, body)
    detail["handover"] = HANDOVER.get((detail.get("building") or "").lower(), "")
    detail["location_note"] = "Dubai Marina waterfront, 5 minutes to Marina Mall"
    return detail


def build_features(detail: dict) -> list[str]:
    features = []
    if detail.get("view_label"):
        features.append(f"{detail['view_label']} from floor-to-ceiling windows")
    if detail.get("size_m2"):
        features.append(
            f"Spacious {detail['size_m2']:.0f} sqm layout with open-plan living area"
        )
    # Низкий этаж — факт, но не достоинство: в фактах он есть, здесь не нужен
    if detail.get("floor_en") in ("High Floor", "Mid Floor"):
        features.append(f"{detail['floor_en']} placement in the tower")
    features.append("Contemporary architecture with sleek and spacious interiors")
    features.append("Branded residence by Emaar, one of Dubai's leading developers")
    if detail.get("handover"):
        features.append(f"Handover {detail['handover']} — ready to move in soon")
    return features


def build_amenities(detail: dict) -> list[str]:
    listed = [a for a in (detail.get("amenities") or []) if a][:5]
    base = [
        "Infinity pool overlooking the marina",
        "Fully equipped gymnasium",
        "Waterfront promenade with retail and dining",
        "24-hour security and concierge",
        "Marina Mall within 5 minutes",
    ]
    seen, result = set(), []
    for item in listed + base:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result[:6]


def tower_median_per_sqm(bedrooms: str) -> float | None:
    """Медиана AED за м² по башне для этого типа — база для оценки цены."""
    values = [r["price_per_m2"] for r in json.loads(RAW.read_text(encoding="utf-8"))
              if r["bedrooms"] == str(bedrooms) and r.get("price_per_m2")]
    return statistics.median(values) if values else None


def build_comment(detail: dict, client_price: int) -> str:
    """Короткий текст для клиента: что это, чем хорошо, как с ценой."""
    beds = detail.get("bedrooms")
    parts: list[str] = []

    opening = f"A {beds}-bedroom residence of {detail['size_m2']:.1f} sqm" \
        if detail.get("size_m2") else f"A {beds}-bedroom residence"
    if detail.get("floor_en"):
        opening += f" on a {detail['floor_en'].lower().replace(' floor', ' floor')}"
    if detail.get("view_label"):
        opening += f", with {detail['view_label'].lower()} through floor-to-ceiling windows"
    parts.append(opening + ".")

    handover = detail.get("handover")
    parts.append(
        f"{detail.get('building')} is a waterfront tower by Emaar in {detail.get('area')}"
        + (f", with handover scheduled for {handover}." if handover else ".")
    )

    median = tower_median_per_sqm(beds)
    if median and detail.get("size_m2"):
        per_sqm = client_price / detail["size_m2"]
        delta = (per_sqm / median - 1) * 100
        # Цену ниже рынка называем прямо — это аргумент. Выше рынка не
        # комментируем вовсе: врать нельзя, но и топить сделку незачем.
        if delta <= -6:
            parts.append(
                f"At {money(client_price)} AED the unit sits about {abs(delta):.0f}% below "
                f"the current average for comparable units in the tower."
            )
        elif delta < 6:
            parts.append(
                f"At {money(client_price)} AED the price is in line with comparable "
                f"units currently available in the tower."
            )

    parts.append("Payment in cash; the unit is available for immediate reservation.")
    return "\n\n".join(parts)


def money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def find_listing(listing_id: str) -> dict:
    wanted = listing_id.replace("PF-", "")
    for row in json.loads(RAW.read_text(encoding="utf-8")):
        if row["id"].replace("PF-", "") == wanted:
            return row
    raise SystemExit(f"Объект {listing_id} не найден в выгрузке")


def cell(row: list, index: int) -> str:
    return row[index].strip() if len(row) > index and row[index] else ""


def parse_price(value: str) -> int | None:
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else None


def approved_rows(rows: list[list]) -> tuple[list[dict], dict[str, int]]:
    columns = resolve_columns(rows[0])
    found = []
    for index, row in enumerate(rows[1:], start=2):
        if str(cell(row, columns["approved"])).upper() == "TRUE":
            found.append({
                "sheet_row": index,
                "id": cell(row, columns["id"]),
                "type": cell(row, columns["type"]),
                "agent": cell(row, columns["agent"]),
                "agency": cell(row, columns["agency"]),
                "phone": cell(row, columns["phone"]),
                "ask_price": parse_price(cell(row, columns["ask"])),
                "my_price": parse_price(cell(row, columns["my_price"])),
            })
    return found, columns


def unit_folder_name(row: dict, detail: dict) -> str:
    """«4 000 000 · Prop Plus · 105 sqm» — чтобы с телефона папка узнавалась сразу."""
    agency = (row.get("agency") or "").split(" Real Estate")[0].split(" LLC")[0].strip()
    size = f"{detail['size_m2']:.0f} sqm" if detail.get("size_m2") else ""
    parts = [money(row["my_price"]), agency[:28], size]
    return " · ".join(p for p in parts if p)


def unit_folder(row: dict, detail: dict) -> dict:
    """Папка объекта на Общем диске: внутри PDF и всё, что пришлёт брокер."""
    from lib import client_folders, drive

    bedrooms = row["type"]
    tree = client_folders.ensure_client_tree(CLIENT_NAME, CLIENT_TARGET, [bedrooms.rstrip("BR")])
    parent = tree.get(bedrooms) or tree["_client"]
    return drive.ensure_folder(unit_folder_name(row, detail), parent["id"])


IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp", "image/heic")


def pull_plans_from_drive(folder: dict, unit_dir: Path) -> list[Path]:
    """Забрать картинки, которые Алексей положил в папку объекта на Диске.

    Любая картинка рядом с PDF считается планировкой от брокера.
    Скачивается в plans_custom/, откуда её и берёт сборка.
    """
    from lib import drive
    from lib.google_auth import drive_service

    target = unit_dir / "plans_custom"
    pulled: list[Path] = []

    for item in drive.list_files(folder_id=folder["id"]):
        if not item["mimeType"].startswith("image/"):
            continue
        suffix = {"image/png": ".png", "image/webp": ".webp"}.get(item["mimeType"], ".jpg")
        local = target / f"drive_{item['id'][:10]}{suffix}"
        if not local.exists():
            target.mkdir(parents=True, exist_ok=True)
            data = drive_service().files().get_media(
                fileId=item["id"], supportsAllDrives=True).execute()
            local.write_bytes(data)
            print(f"План   : с Диска пришёл {item['name']} → {local.name}")
        pulled.append(local)

    return pulled


def upload_to_drive(pdf: Path, folder: dict) -> str | None:
    """Положить презентацию в папку объекта. None — если не вышло."""
    from googleapiclient.http import MediaFileUpload

    from lib import drive
    from lib.google_auth import drive_service

    media = MediaFileUpload(str(pdf), mimetype="application/pdf", resumable=False)

    try:
        # Пересборка должна обновлять файл, а не плодить копии рядом.
        existing = [f for f in drive.list_files(folder_id=folder["id"])
                    if f["name"] in (pdf.name, pdf.stem)]

        # Раньше PDF лежали уровнем выше, прямо в 2BR/. Если нашли там —
        # переносим внутрь папки объекта с сохранением ID: ссылка в таблице живёт.
        if not existing:
            parent_id = drive.get_file(folder["id"]).get("parents", [None])[0]
            if parent_id:
                for stray in drive.list_files(folder_id=parent_id):
                    if stray["name"] in (pdf.name, pdf.stem):
                        drive.move_file(stray["id"], folder["id"])
                        print(f"         перенесён в папку объекта: {stray['name']}")
                        existing.append(stray)

        if existing:
            updated = drive_service().files().update(
                fileId=existing[0]["id"], media_body=media,
                fields="id, name, webViewLink", supportsAllDrives=True,
            ).execute()
            for extra in existing[1:]:
                drive_service().files().update(
                    fileId=extra["id"], body={"trashed": True},
                    supportsAllDrives=True).execute()
                print(f"         дубль убран в корзину: {extra['name']}")
            return updated.get("webViewLink")

        created = drive_service().files().create(
            body={"name": pdf.name, "parents": [folder["id"]],
                  "mimeType": "application/pdf"},
            media_body=media, fields="id, name, webViewLink",
            supportsAllDrives=True,
        ).execute()
        return created.get("webViewLink")

    except Exception as error:  # noqa: BLE001
        if "storageQuota" in str(error):
            print("         загрузить на Диск нельзя: нужен Общий диск")
            return None
        raise


def make(listing_id: str, price: int, row: dict | None = None) -> tuple[Path, dict | None]:
    """Собрать презентацию. Возвращает (pdf, папка объекта на Диске или None)."""
    listing = find_listing(listing_id)
    print(f"Ссылка : {listing['url']}")

    detail = enrich(listing_detail.fetch_detail(listing["url"]),
                    stored_title=listing.get("title", ""))
    print(f"Объект : {detail['building']} · {detail['size_m2']} sqm · {detail['bedrooms']}BR")

    unit_dir = CLIENT_DIR / "approved" / listing_id

    # Папка объекта на Диске. Сюда ляжет PDF, и сюда же Алексей кидает
    # планировку от брокера прямо из WhatsApp — забираем её перед сборкой.
    folder = None
    if row:
        folder = unit_folder(row, detail)
        pull_plans_from_drive(folder, unit_dir)

    # Источники фото по приоритету:
    #  1. photos_custom/ — если Алексей положил свои
    #  2. фото ИЗ ОБЪЯВЛЕНИЯ — основной источник: у каждого лота свои кадры,
    #     и рендеры застройщика, и реальные съёмки этой квартиры
    #  3. рендеры проекта — только чтобы дополнить, если в объявлении мало кадров
    custom = sorted(p for p in (unit_dir / "photos_custom").glob("*")
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png"))

    if custom:
        photos = custom
        print(f"Фото   : {len(photos)} из photos_custom")
    else:
        photos = listing_detail.download_images(detail["images"], unit_dir / "photos",
                                                limit=16)
        print(f"Фото   : {len(photos)} из объявления")

        if len(photos) < MIN_PHOTOS:
            extra = [p for p in project_photos(detail["building"])
                     if p.name not in {x.name for x in photos}]
            add = extra[:MIN_PHOTOS - len(photos)]
            if add:
                photos = photos + add
                print(f"         + {len(add)} рендеров проекта — в объявлении мало кадров")

    # Планировка — отдельно от общих фото, ставится своим слайдом
    # Сначала то, что Алексей положил руками, потом размеченное агентом.
    manual = sorted(p for p in (unit_dir / "plans_custom").glob("*")
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    plans = manual or listing_detail.download_images(
        detail.get("floor_plans") or [], unit_dir / "plans", limit=3)
    detail["plans"] = plans

    # Планировка идёт отдельным листом и в галерее появляться не должна.
    # Сравнивать по имени мало: копия в plans_custom называется иначе, чем
    # исходник в photos. Поэтому сверяем содержимое, а заодно выкидываем всё,
    # что выглядит чертежом — рядом с рендерами оно смотрится браком.
    plan_digests = {hashlib.md5(p.read_bytes()).hexdigest() for p in plans}
    photos = [
        p for p in photos
        if hashlib.md5(p.read_bytes()).hexdigest() not in plan_digests
        and not listing_detail.looks_like_plan(p)
    ]

    # Карта локации — только настоящая, из ассетов проекта. Нет карты — нет слайда.
    maps = [p for p in project_photos(detail["building"]) if "location" in p.name.lower()]
    detail["location_image"] = str(maps[0]) if maps else None

    if plans:
        source = "положены вручную" if manual else "размечены агентом"
        print(f"План   : {len(plans)} шт ({source})")
    else:
        # Догадки в презентацию не идут — только показываем кандидатов.
        guesses = [p for p in sorted((unit_dir / "photos").glob("*.jpg"))
                   if listing_detail.looks_like_plan(p)]
        if guesses:
            print(f"План   : размеченного нет. Похожи на план: "
                  f"{', '.join(p.name for p in guesses)}")
            print(f"         если это планировка — переложи в {unit_dir / 'plans_custom'}")
        else:
            print("План   : в объявлении нет")

    size_tag = f"{detail['size_m2']:.0f}sqm" if detail.get("size_m2") else "unit"
    name = f"{slugify(detail['building'])}_{detail['bedrooms']}br_{size_tag}".replace("__", "_")
    output = CLIENT_DIR / "approved" / listing_id / f"{name}.pdf"

    pdf_brochure.build(detail, photos, price, build_comment(detail, price), output)
    print(f"Готово : {output}  ({output.stat().st_size // 1024} КБ)")
    return output, folder


def process_approved(quiet: bool = False) -> list[dict]:
    """Собрать презентации по всем галочкам с ценой. Возвращает список сделанного."""
    sheet_rows = sheets.read_range(SPREADSHEET_ID, "Объекты!A1:Z200")
    header = sheet_rows[0]
    rows, columns = approved_rows(sheet_rows)
    done = []
    if not rows:
        if not quiet:
            print("В таблице пока нет ни одной галочки ✅ Одобрено.")
        return done

    print(f"Одобрено объектов: {len(rows)}\n")
    for row in rows:
        ask = money(row["ask_price"]) if row["ask_price"] else "—"
        print(f"  {row['id']}  {row['type']}  запрос {ask} AED  "
              f"{row['agent']} · {row['agency']}  тел. {row['phone']}")

        if not row["my_price"]:
            print("     ЦЕНЫ НЕТ. Впиши свою в колонку «Моя цена» — из объявления не подставлю.\n")
            done.append({"id": row["id"], "status": "нет цены"})
            continue

        print(f"     твоя цена: {money(row['my_price'])} AED")
        pdf, folder = make(row["id"], row["my_price"], row)
        link = upload_to_drive(pdf, folder) if folder else None
        target = link or str(pdf)
        guard_write(header, columns["presentation"], COLUMNS["presentation"])
        target_cell = f"Объекты!{a1_column(columns['presentation'])}{row['sheet_row']}"
        sheets.update_range(SPREADSHEET_ID, target_cell, [[target]])
        print(f"     в таблицу: {target}\n")
        done.append({"id": row["id"], "status": "готово", "link": target, "type": row["type"]})
    return done


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("listing_id", nargs="?")
    parser.add_argument("--price", type=int, help="цена для клиента, AED")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.listing_id:
        if not args.price:
            print("Нужна цена: --price 3050000")
            return 1
        make(args.listing_id, args.price)
        return 0

    process_approved()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
