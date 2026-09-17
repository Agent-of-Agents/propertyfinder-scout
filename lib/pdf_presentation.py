"""PDF-презентация объекта для клиента.

Формат и стиль — по образцу Алексея: слайды 16:9 (960×540 pt), чёрный фон,
Montserrat капсом в заголовках, Calibri в тексте, медные квадратные буллеты.
Горизонтальный формат открывается на телефоне во весь экран.

Контактов и логотипа нет — так утверждено.
Весь текст английский: презентация уходит клиенту напрямую.
"""

from __future__ import annotations

import io
from pathlib import Path

from fpdf import FPDF
from PIL import Image, ImageEnhance

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = PROJECT_ROOT / "assets" / "fonts"
WIN_FONTS = Path("C:/Windows/Fonts")

W, H = 960.0, 540.0            # 16:9, как в образце
PAD = 52.0

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
MUTED = (188, 188, 190)
ACCENT = (163, 94, 65)         # медь, снята из образца
RULE = (58, 58, 60)

AED_TO_USD = 3.6725


def _fit(path: Path, box_w: float, box_h: float, darken: float = 1.0,
         dpi_scale: float = 2.6) -> io.BytesIO:
    """Обрезать фото под пропорции блока без искажений, при желании притемнить."""
    image = Image.open(path).convert("RGB")
    target = box_w / box_h
    current = image.width / image.height

    if current > target:
        new_w = int(image.height * target)
        left = (image.width - new_w) // 2
        image = image.crop((left, 0, left + new_w, image.height))
    else:
        new_h = int(image.width / target)
        top = int((image.height - new_h) * 0.35)
        image = image.crop((0, top, image.width, top + new_h))

    max_w = int(box_w * dpi_scale)
    if image.width > max_w:
        image = image.resize((max_w, max(1, int(max_w / target))), Image.LANCZOS)

    if darken < 1.0:
        image = ImageEnhance.Brightness(image).enhance(darken)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=86)
    buffer.seek(0)
    return buffer


def _contain(path: Path, box_w: float, box_h: float) -> tuple[io.BytesIO, float, float]:
    """Вписать целиком, не обрезая. Для планировок обрезка недопустима."""
    image = Image.open(path).convert("RGB")
    scale = min(box_w / image.width, box_h / image.height)
    width, height = image.width * scale, image.height * scale

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    buffer.seek(0)
    return buffer, width, height


class Deck(FPDF):
    def __init__(self) -> None:
        # orientation="P" — формат задан явно как 960×540, разворачивать не надо
        super().__init__(orientation="P", unit="pt", format=(W, H))
        self.set_auto_page_break(False)
        self.set_margins(0, 0, 0)

        self.add_font("mont", "", str(FONT_DIR / "Montserrat-Light.ttf"))
        self.add_font("montm", "", str(FONT_DIR / "Montserrat-Regular.ttf"))
        # Calibri есть только на Windows; в контейнере — Montserrat из assets/
        body = WIN_FONTS / "calibri.ttf"
        bodyb = WIN_FONTS / "calibrib.ttf"
        self.add_font("body", "", str(body if body.exists() else FONT_DIR / "Montserrat-Regular.ttf"))
        self.add_font("bodyb", "", str(bodyb if bodyb.exists() else FONT_DIR / "Montserrat-Medium.ttf"))

    # ------------------------------------------------------------ примитивы

    def slide(self) -> None:
        self.add_page()
        self.set_fill_color(*BLACK)
        self.rect(0, 0, W, H, style="F")

    def caps_title(self, text: str, x: float, y: float, size: float = 30,
                   width: float | None = None, align: str = "L") -> float:
        """Заголовок капсом с разрядкой — как в образце."""
        self.set_font("mont", "", size)
        self.set_text_color(*WHITE)
        self.set_char_spacing(size * 0.045)
        self.set_xy(x, y)
        self.cell(width if width is not None else W - 2 * x, size * 1.25,
                  text.upper(), align=align)
        self.set_char_spacing(0)
        return y + size * 1.25

    def bullets(self, items: list[str], x: float, y: float, width: float,
                size: float = 15.5, gap: float = 9.0) -> float:
        """Список с медными квадратными маркерами."""
        for item in items:
            self.set_fill_color(*ACCENT)
            self.rect(x, y + size * 0.34, 5, 5, style="F")

            self.set_font("body", "", size)
            self.set_text_color(*WHITE)
            self.set_xy(x + 15, y)
            self.multi_cell(width - 15, size * 1.28, item, align="L")
            y = self.get_y() + gap
        return y


def _fact_lines(detail: dict, price_aed: int) -> list[str]:
    """Ключевые факты — порядок и формулировки как в образце."""
    lines = []
    if detail.get("size_sqft") and detail.get("size_m2"):
        lines.append(f"Size: {detail['size_sqft']:,.0f} sqft ({detail['size_m2']:.2f} sqm)")
    usd = price_aed / AED_TO_USD
    lines.append(f"{price_aed:,.0f} AED ({usd:,.0f} USD)".replace(",", " "))
    beds = detail.get("bedrooms")
    baths = detail.get("bathrooms")
    if beds:
        text = f"{beds} bedroom" + ("s" if str(beds) not in ("1", "studio") else "")
        if baths:
            text += f" and {baths} bathroom" + ("s" if str(baths) != "1" else "")
        lines.append(text)
    if detail.get("view_label"):
        lines.append(detail["view_label"])
    if detail.get("floor_en"):
        lines.append(detail["floor_en"])
    if detail.get("handover"):
        lines.append(f"Handover: {detail['handover']}")
    return lines


def build(detail: dict, photos: list[Path], client_price: int,
          features: list[str], amenities: list[str], output: Path) -> Path:
    """Собрать презентацию. client_price — цена, которую назвал Алексей."""
    pdf = Deck()
    pool = list(photos)

    def take(index: int) -> Path | None:
        return pool[index] if index < len(pool) else None

    # ------------------------------------------------------------- 1. обложка
    pdf.slide()
    cover = take(0)
    if cover:
        pdf.image(_fit(cover, W, H, darken=0.42), 0, 0, W, H)
    pdf.set_font("mont", "", 44)
    pdf.set_text_color(*WHITE)
    pdf.set_char_spacing(2.6)
    pdf.set_xy(0, H / 2 - 62)
    pdf.cell(W, 52, (detail.get("building") or "").upper(), align="C")
    pdf.set_xy(0, H / 2 - 8)
    pdf.cell(W, 52, (detail.get("area") or "").upper(), align="C")
    pdf.set_char_spacing(0)

    # -------------------------------------------------------- 2. ключевые факты
    pdf.slide()
    photo_h = H * 0.60
    hero = take(3) or cover
    if hero:
        pdf.image(_fit(hero, W, photo_h), 0, 0, W, photo_h)

    facts = _fact_lines(detail, client_price)
    box_y = photo_h + 22
    box_h = H - box_y - 24
    box_w = W * 0.55
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(1)
    pdf.rect(PAD, box_y, box_w, box_h)
    pdf.bullets(facts, PAD + 20, box_y + 14, box_w - 40, size=14.5, gap=5.5)

    # ------------------------------------------------------ 3. property features
    pdf.slide()
    split = W * 0.53
    left = take(1)
    if left:
        pdf.image(_fit(left, split, H), 0, 0, split, H)
    y = pdf.caps_title("Property Features", split + 40, 62, size=30,
                       width=W - split - 80)
    pdf.bullets(features, split + 40, y + 26, W - split - 80)

    # --------------------------------------------------------- 4-6. интерьеры
    for index in (4, 5, 6):
        photo = take(index)
        if not photo:
            continue
        pdf.slide()
        pdf.image(_fit(photo, W, H), 0, 0, W, H)

    # ------------------------------------------------------------ планировка
    for plan in (detail.get("plans") or [])[:2]:
        pdf.slide()
        pdf.caps_title("Floor Plan", PAD, 40, size=26)
        box_w, box_h = W - 2 * PAD, H - 130
        try:
            stream, width, height = _contain(plan, box_w, box_h)
        except Exception:  # noqa: BLE001
            continue
        pdf.image(stream, (W - width) / 2, 100 + (box_h - height) / 2, width, height)

    # -------------------------------------------------------- 7. удобства дома
    if amenities:
        pdf.slide()
        right = take(7)
        if right:
            pdf.image(_fit(right, W - split, H), split, 0, W - split, H)
        y = pdf.caps_title("Building & Amenities", PAD, 62, size=30, width=split - 80)
        pdf.bullets(amenities, PAD, y + 26, split - 80)

    # ------------------------------------------------------------ 8. локация
    # Только если есть настоящая карта. Подставлять вместо неё случайное фото
    # (а тем более планировку) нельзя — слайд назван Location.
    location = detail.get("location_image")
    if location and Path(location).exists():
        pdf.slide()
        pdf.image(_fit(Path(location), W, H * 0.78), 0, 0, W, H * 0.78)
        pdf.caps_title("Location", PAD, H * 0.78 + 26, size=24)
        if detail.get("location_note"):
            pdf.set_font("body", "", 14)
            pdf.set_text_color(*MUTED)
            pdf.set_xy(PAD + 260, H * 0.78 + 32)
            pdf.cell(W - PAD - 280, 20, detail["location_note"])

    # ------------------------------------------------------------ 9. финальный
    closing = take(2) or take(0)
    if closing:
        pdf.slide()
        pdf.image(_fit(closing, W, H, darken=0.55), 0, 0, W, H)
        pdf.set_font("mont", "", 30)
        pdf.set_text_color(*WHITE)
        pdf.set_char_spacing(2.2)
        pdf.set_xy(0, H / 2 - 26)
        pdf.cell(W, 40, (detail.get("building") or "").upper(), align="C")
        pdf.set_char_spacing(0)

    output.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output))
    return output
