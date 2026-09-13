"""Презентация объекта — светлая вёрстка.

Белый лист, крупные фотографии, минимум текста. Показываются ВСЕ фото
из объявления: галерея добирает столько разворотов, сколько нужно.

Слайды 16:9 (960×540) — открывается на телефоне во весь экран.
Ни контактов, ни логотипов: файл уходит клиенту напрямую.
"""

from __future__ import annotations

import io
from pathlib import Path

from fpdf import FPDF
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = PROJECT_ROOT / "assets" / "fonts"
WIN_FONTS = Path("C:/Windows/Fonts")

W, H = 960.0, 540.0
M = 46.0                       # поле
G = 11.0                       # зазор между фото

PAPER = (255, 255, 255)
PANEL = (246, 245, 242)
INK = (23, 23, 23)
MUTED = (122, 122, 122)
HAIR = (223, 221, 217)
ACCENT = (163, 94, 65)

AED_TO_USD = 3.6725


def _fit(path: Path, box_w: float, box_h: float, scale: float = 2.4) -> io.BytesIO:
    """Заполнить блок целиком, обрезав лишнее по центру."""
    image = Image.open(path).convert("RGB")
    target, current = box_w / box_h, image.width / image.height

    if current > target:
        new_w = int(image.height * target)
        image = image.crop(((image.width - new_w) // 2, 0,
                            (image.width - new_w) // 2 + new_w, image.height))
    else:
        new_h = int(image.width / target)
        top = int((image.height - new_h) * 0.35)
        image = image.crop((0, top, image.width, top + new_h))

    max_w = int(box_w * scale)
    if image.width > max_w:
        image = image.resize((max_w, max(1, int(max_w / target))), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=87)
    buffer.seek(0)
    return buffer


def _contain(path: Path, box_w: float, box_h: float) -> tuple[io.BytesIO, float, float]:
    """Вписать целиком, не обрезая — для планировок."""
    image = Image.open(path).convert("RGB")
    k = min(box_w / image.width, box_h / image.height)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=93)
    buffer.seek(0)
    return buffer, image.width * k, image.height * k


def money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


class Brochure(FPDF):
    def __init__(self) -> None:
        super().__init__(orientation="P", unit="pt", format=(W, H))
        self.set_auto_page_break(False)
        self.set_margins(0, 0, 0)
        self.add_font("display", "", str(FONT_DIR / "Montserrat-Light.ttf"))
        self.add_font("label", "", str(FONT_DIR / "Montserrat-Regular.ttf"))
        self.add_font("body", "", str(WIN_FONTS / "calibri.ttf"))
        self.add_font("bodyb", "", str(WIN_FONTS / "calibrib.ttf"))

    def sheet(self) -> None:
        self.add_page()
        self.set_fill_color(*PAPER)
        self.rect(0, 0, W, H, style="F")

    def eyebrow(self, text: str, x: float, y: float, color=MUTED, size: float = 7.5) -> None:
        self.set_font("label", "", size)
        self.set_text_color(*color)
        self.set_char_spacing(size * 0.22)
        self.set_xy(x, y)
        self.cell(400, size * 1.4, text.upper())
        self.set_char_spacing(0)

    def display(self, text: str, x: float, y: float, size: float = 30,
                width: float = 600, align: str = "L", color=INK) -> None:
        self.set_font("display", "", size)
        self.set_text_color(*color)
        self.set_char_spacing(size * 0.05)
        self.set_xy(x, y)
        self.cell(width, size * 1.2, text.upper(), align=align)
        self.set_char_spacing(0)

    def hairline(self, x: float, y: float, width: float) -> None:
        self.set_draw_color(*HAIR)
        self.set_line_width(0.6)
        self.line(x, y, x + width, y)

    def fact_row(self, label: str, value: str, x: float, y: float, width: float) -> float:
        self.set_font("body", "", 10.5)
        self.set_text_color(*MUTED)
        self.set_xy(x, y)
        self.cell(width * 0.45, 15, label)

        self.set_font("bodyb", "", 11.5)
        self.set_text_color(*INK)
        self.set_xy(x + width * 0.45, y)
        self.cell(width * 0.55, 15, value)

        self.hairline(x, y + 19, width)
        return y + 27


# --------------------------------------------------------------------- галерея

def _gallery_patterns(count: int) -> list[list[tuple[float, float, float, float]]]:
    """Разбить N фото на развороты с разными сетками, чтобы не было монотонности."""
    inner_w, inner_h = W - 2 * M, H - 2 * M

    def big_plus_two() -> list[tuple[float, float, float, float]]:
        big_w = inner_w * 0.615
        small_w = inner_w - big_w - G
        small_h = (inner_h - G) / 2
        return [
            (M, M, big_w, inner_h),
            (M + big_w + G, M, small_w, small_h),
            (M + big_w + G, M + small_h + G, small_w, small_h),
        ]

    def two_up() -> list[tuple[float, float, float, float]]:
        half = (inner_w - G) / 2
        return [(M, M, half, inner_h), (M + half + G, M, half, inner_h)]

    def three_row() -> list[tuple[float, float, float, float]]:
        third = (inner_w - 2 * G) / 3
        return [(M + i * (third + G), M, third, inner_h) for i in range(3)]

    def four_grid() -> list[tuple[float, float, float, float]]:
        half_w = (inner_w - G) / 2
        half_h = (inner_h - G) / 2
        return [
            (M, M, half_w, half_h), (M + half_w + G, M, half_w, half_h),
            (M, M + half_h + G, half_w, half_h),
            (M + half_w + G, M + half_h + G, half_w, half_h),
        ]

    def one_full() -> list[tuple[float, float, float, float]]:
        return [(M, M, inner_w, inner_h)]

    cycle = [big_plus_two, four_grid, two_up, big_plus_two, three_row, four_grid]

    pages, left, index = [], count, 0
    while left > 0:
        if left == 1:
            pages.append(one_full())
            break
        if left == 2:
            pages.append(two_up())
            break
        if left == 3:
            pages.append(big_plus_two())
            break

        boxes = cycle[index % len(cycle)]()
        if len(boxes) > left:
            boxes = four_grid()[:left] if left == 4 else big_plus_two()[:left]
        pages.append(boxes)
        left -= len(boxes)
        index += 1

    return pages


# ----------------------------------------------------------------------- сборка

def build(detail: dict, photos: list[Path], client_price: int,
          comment: str, output: Path) -> Path:
    pdf = Brochure()
    photos = [p for p in photos if p.exists()]
    price_text = f"{money(client_price)} AED"
    usd_text = f"≈ {money(client_price / AED_TO_USD)} USD"

    # -------------------------------------------------------------- 1. обложка
    pdf.sheet()
    band_h = 132
    if photos:
        pdf.image(_fit(photos[0], W, H - band_h), 0, 0, W, H - band_h)

    band_y = H - band_h
    pdf.set_fill_color(*PAPER)
    pdf.rect(0, band_y, W, band_h, style="F")

    pdf.display(detail.get("building") or "", M, band_y + 30, size=31, width=520)
    pdf.eyebrow(detail.get("area") or "", M, band_y + 76)

    pdf.set_font("display", "", 27)
    pdf.set_text_color(*INK)
    pdf.set_xy(W - M - 340, band_y + 32)
    pdf.cell(340, 32, price_text, align="R")

    pdf.set_font("body", "", 10.5)
    pdf.set_text_color(*MUTED)
    pdf.set_xy(W - M - 340, band_y + 68)
    pdf.cell(340, 14, usd_text, align="R")

    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(1.4)
    pdf.line(M, band_y + 22, M + 44, band_y + 22)

    # ------------------------------------------------------------- 2. описание
    pdf.sheet()
    split = W * 0.47
    if len(photos) > 1:
        pdf.image(_fit(photos[1], W - split, H), split, 0, W - split, H)

    pdf.eyebrow("The Residence", M, M + 6, color=ACCENT)
    pdf.display(f"{detail.get('bedrooms')} Bedroom Apartment", M, M + 28,
                size=20, width=split - M - 30)

    y = M + 84
    inner = split - M - 34
    if detail.get("size_m2"):
        y = pdf.fact_row("Size",
                         f"{detail['size_sqft']:,.0f} sqft · {detail['size_m2']:.1f} sqm",
                         M, y, inner)
    if detail.get("bathrooms"):
        y = pdf.fact_row("Bathrooms", str(detail["bathrooms"]), M, y, inner)
    if detail.get("view_label"):
        y = pdf.fact_row("View", detail["view_label"], M, y, inner)
    if detail.get("floor_en"):
        y = pdf.fact_row("Floor", detail["floor_en"], M, y, inner)
    if detail.get("handover"):
        y = pdf.fact_row("Handover", detail["handover"], M, y, inner)

    pdf.eyebrow("Price", M, y + 10, color=ACCENT)
    pdf.set_font("display", "", 24)
    pdf.set_text_color(*INK)
    pdf.set_xy(M, y + 26)
    pdf.cell(inner, 28, price_text)

    # -------------------------------------------------------------- 3. галерея
    rest = photos[2:]
    for boxes in _gallery_patterns(len(rest)):
        pdf.sheet()
        for (x, y_box, w, h) in boxes:
            if not rest:
                break
            pdf.image(_fit(rest.pop(0), w, h), x, y_box, w, h)

    # ----------------------------------------------------------- 4. планировка
    for plan in (detail.get("plans") or [])[:2]:
        pdf.sheet()
        pdf.eyebrow("Floor Plan", M, M, color=ACCENT)
        box_w, box_h = W - 2 * M, H - 2 * M - 34
        try:
            stream, width, height = _contain(Path(plan), box_w, box_h)
        except Exception:  # noqa: BLE001
            continue
        pdf.image(stream, (W - width) / 2, M + 34 + (box_h - height) / 2, width, height)

    # ------------------------------------------------------------ 5. заключение
    pdf.sheet()
    pdf.set_fill_color(*PANEL)
    pdf.rect(0, 0, W, H, style="F")

    pdf.eyebrow("Notes", M, M + 10, color=ACCENT)
    pdf.display(detail.get("building") or "", M, M + 32, size=22, width=W - 2 * M)

    pdf.set_font("body", "", 13)
    pdf.set_text_color(*INK)
    pdf.set_xy(M, M + 80)
    pdf.multi_cell(W * 0.56 - M, 19, comment)

    box_x = W * 0.60
    box_w = W - M - box_x
    pdf.set_draw_color(*HAIR)
    pdf.set_line_width(0.8)
    pdf.rect(box_x, M + 74, box_w, 150)

    pdf.eyebrow("Asking price", box_x + 24, M + 100, color=MUTED)
    pdf.set_font("display", "", 26)
    pdf.set_text_color(*INK)
    pdf.set_xy(box_x + 24, M + 122)
    pdf.cell(box_w - 48, 30, price_text)

    pdf.set_font("body", "", 11)
    pdf.set_text_color(*MUTED)
    pdf.set_xy(box_x + 24, M + 158)
    pdf.cell(box_w - 48, 15, usd_text)

    if detail.get("size_m2"):
        pdf.set_xy(box_x + 24, M + 180)
        pdf.cell(box_w - 48, 15,
                 f"{money(client_price / detail['size_m2'])} AED per sqm")

    # Полоса из трёх кадров — чтобы низ последнего листа не пустовал
    strip = [p for p in photos[1:] if p.exists()][:3]
    if len(strip) == 3:
        strip_h = 118.0
        strip_y = H - M - 26 - strip_h
        strip_w = (W - 2 * M - 2 * G) / 3
        for index, photo in enumerate(strip):
            pdf.image(_fit(photo, strip_w, strip_h),
                      M + index * (strip_w + G), strip_y, strip_w, strip_h)

    pdf.set_font("body", "", 8)
    pdf.set_text_color(*MUTED)
    pdf.set_xy(M, H - M - 12)
    pdf.cell(W - 2 * M, 12,
             "Information is accurate at the date of preparation and does not "
             "constitute a public offer.")

    output.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output))
    return output
