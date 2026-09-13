"""Приложить планировку (или фото) от брокера к объекту и пересобрать презентацию.

Алексей получает планировку в WhatsApp, пересылает себе в Telegram «Избранное»,
Telegram Desktop кладёт файл в Downloads\\Telegram Desktop. Дальше одна команда:

    attach_plan.py "Dhanvanthri"                     — самая свежая картинка из Telegram Desktop
    attach_plan.py "+971 58 562-98-22"
    attach_plan.py "https://www.propertyfinder.ae/…/marina-shores-141439708.html"
    attach_plan.py "4 000 000"
    attach_plan.py "Prop Plus" --file "C:\\путь\\план.jpg"   — конкретный файл
    attach_plan.py "Prop Plus" --phone "+971 50 123-45-67"   — записать личный номер брокера
    attach_plan.py "Prop Plus" --as-photo                     — это не план, а фото квартиры

Зацепка — любая: имя брокера, агентство, телефон, ссылка, ID, цена.
Ищем сначала среди одобренных строк, потом по всей таблице.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lib import sheets  # noqa: E402
from scripts import make_presentation as mp  # noqa: E402

TELEGRAM_DIR = Path.home() / "Downloads" / "Telegram Desktop"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
FRESH_SECONDS = 6 * 3600     # свежей картинкой считаем ту, что моложе шести часов


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def newest_image(folder: Path) -> Path | None:
    candidates = [p for p in folder.iterdir()
                  if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file()]
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    age = time.time() - newest.stat().st_mtime
    if age > FRESH_SECONDS:
        hours = age / 3600
        print(f"Внимание: самая свежая картинка в Telegram Desktop — {newest.name}, "
              f"ей уже {hours:.0f} ч. Если это не она, укажи --file.")
    return newest


def match_row(hint: str, rows: list[list], columns: dict[str, int]) -> list[dict]:
    """Найти строки таблицы по зацепке. Возвращает список кандидатов."""
    hint_l = hint.lower().strip()
    hint_d = digits(hint)
    hint_id = re.search(r"(\d{8,})", hint)
    hint_id = hint_id.group(1) if hint_id and ("propertyfinder" in hint_l or hint_d == hint_id.group(1)) else None

    found = []
    for index, row in enumerate(rows[1:], start=2):
        get = lambda key: mp.cell(row, columns[key])  # noqa: E731
        listing_id = get("id")
        approved = str(get("approved")).upper() == "TRUE"

        score = 0
        if hint_id and listing_id.endswith(hint_id):
            score = 100
        elif hint_d and len(hint_d) >= 7 and hint_d in digits(get("phone")):
            score = 90
        elif hint_d and len(hint_d) >= 6 and hint_d in (digits(get("my_price")), digits(get("ask"))):
            score = 60
        elif len(hint_l) >= 3 and (hint_l in get("agent").lower() or hint_l in get("agency").lower()):
            score = 70

        if score:
            found.append({
                "score": score + (5 if approved else 0),
                "sheet_row": index,
                "id": listing_id,
                "type": get("type"),
                "agent": get("agent"),
                "agency": get("agency"),
                "phone": get("phone"),
                "approved": approved,
                "ask_price": mp.parse_price(get("ask")),
                "my_price": mp.parse_price(get("my_price")),
            })

    found.sort(key=lambda r: -r["score"])
    return found


def describe(row: dict) -> str:
    mark = "✅" if row["approved"] else "  "
    price = mp.money(row["my_price"]) if row["my_price"] else "цены нет"
    return (f"{mark} {row['id']}  {row['type']}  {price} AED  "
            f"{row['agent']} · {row['agency']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("hint", help="брокер / телефон / ссылка / цена")
    parser.add_argument("--file", help="путь к картинке; по умолчанию — свежая из Telegram Desktop")
    parser.add_argument("--phone", help="личный номер брокера — записать в комментарий")
    parser.add_argument("--as-photo", action="store_true",
                        help="приложить как фото квартиры, а не как планировку")
    parser.add_argument("--no-build", action="store_true", help="только приложить, не пересобирать")
    args = parser.parse_args()

    sheet_rows = sheets.read_range(mp.SPREADSHEET_ID, "Объекты!A1:Z200")
    header = sheet_rows[0]
    columns = mp.resolve_columns(header)

    matches = match_row(args.hint, sheet_rows, columns)
    if not matches:
        print(f"По зацепке «{args.hint}» ничего не нашёл в таблице.")
        return 1

    row = matches[0]
    if len(matches) > 1 and matches[1]["score"] == matches[0]["score"]:
        print("Несколько подходящих строк, уточни зацепку:")
        for candidate in matches[:5]:
            print("  " + describe(candidate))
        return 1

    print("Объект : " + describe(row))
    if not row["approved"]:
        print("         (строка не одобрена — приложу, но презентацию собирать не буду)")

    # --- файл
    source = Path(args.file) if args.file else newest_image(TELEGRAM_DIR)
    if not source or not source.exists():
        print("Картинку не нашёл. Укажи --file или проверь папку Telegram Desktop.")
        return 1

    unit_dir = mp.CLIENT_DIR / "approved" / row["id"]
    target_dir = unit_dir / ("photos_custom" if args.as_photo else "plans_custom")
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    target = target_dir / f"{'photo' if args.as_photo else 'plan'}_{stamp}{source.suffix.lower()}"
    shutil.copy2(source, target)
    print(f"Файл   : {source.name}  →  {target.relative_to(mp.CLIENT_DIR)}")

    # --- личный номер брокера — в мой комментарий, не в колонки Алексея
    if args.phone:
        comment_col = mp.column_index(header, "Комментарий системы")
        if comment_col is not None:
            current = mp.cell(sheet_rows[row["sheet_row"] - 1], comment_col)
            note = f"личный WhatsApp брокера: {args.phone}"
            if note not in current:
                mp.guard_write(header, comment_col, "Комментарий системы")
                letter = mp.a1_column(comment_col)
                sheets.update_range(mp.SPREADSHEET_ID, f"Объекты!{letter}{row['sheet_row']}",
                                    [[f"{current}; {note}" if current and current != "—" else note]])
                print(f"Телефон: записал в комментарий строки {row['sheet_row']}")

    # --- пересборка
    if args.no_build or not row["approved"] or not row["my_price"]:
        if row["approved"] and not row["my_price"]:
            print("Цены в «Моя цена» нет — собирать нечего. Впиши и запусти make_presentation.")
        return 0

    pdf, folder = mp.make(row["id"], row["my_price"], row)
    link = mp.upload_to_drive(pdf, folder) if folder else None
    if link:
        mp.guard_write(header, columns["presentation"], mp.COLUMNS["presentation"])
        letter = mp.a1_column(columns["presentation"])
        sheets.update_range(mp.SPREADSHEET_ID, f"Объекты!{letter}{row['sheet_row']}", [[link]])
        print(f"Ссылка : {link}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
