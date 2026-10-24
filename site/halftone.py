"""Фото стрижек -> газетный растр для галереи v2: точки краски на бумаге.

Исходники cut-*.png из Gemini, на выходе cut-*-dots.jpg и цветной cut-*.jpg
того же кадра для наведения. compress.py стрижки пропускает.

    python site/halftone.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

IMG = Path(__file__).resolve().parent / "img"
W, H = 800, 1000
PAPER = (239, 231, 216)
INK = (22, 20, 18)
CELL = 9      # шаг растра в итоговых пикселях; мельче — на телефоне сливается в серое
ANGLE = 45    # угол газетного растра: под 0° глаз видит сетку, а не тон
SS = 3        # суперсэмплинг, иначе края точек лесенкой


def crop(im: Image.Image) -> Image.Image:
    # Голова в кадрах Gemini чуть выше центра, низ — пеньюар, его режем первым
    return ImageOps.fit(im, (W, H), Image.LANCZOS, centering=(0.5, 0.35))


def halftone(im: Image.Image) -> Image.Image:
    gray = ImageOps.autocontrast(im.convert("L"), cutoff=1)
    # Серый студийный фон иначе ложится мелкой точкой по всему листу
    gray = gray.point(lambda v: 255 if v > 200 else v)
    side = int(math.hypot(W, H)) + 2 * CELL
    rot = gray.rotate(ANGLE, expand=True, fillcolor=255)
    ox, oy = (rot.width - side) // 2, (rot.height - side) // 2
    rot = rot.crop((ox, oy, ox + side, oy + side))
    small = rot.resize((side // CELL, side // CELL), Image.BOX)

    canvas = Image.new("L", (side * SS, side * SS), 255)
    draw = ImageDraw.Draw(canvas)
    c = CELL * SS
    for y in range(small.height):
        for x in range(small.width):
            dark = 1 - small.getpixel((x, y)) / 255
            if dark < 0.04:
                continue
            # Площадь точки пропорциональна тону, отсюда корень; 0.75 — точки смыкаются в тенях
            r = c * 0.75 * math.sqrt(dark)
            cx, cy = x * c + c / 2, y * c + c / 2
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=0)

    canvas = canvas.resize((side, side), Image.LANCZOS).rotate(-ANGLE, fillcolor=255)
    ox, oy = (side - W) // 2, (side - H) // 2
    mask = canvas.crop((ox, oy, ox + W, oy + H))
    return Image.composite(Image.new("RGB", (W, H), PAPER), Image.new("RGB", (W, H), INK), mask)


def main() -> None:
    for src in sorted(IMG.glob("cut-*.png")):
        im = crop(Image.open(src).convert("RGB"))
        # Цветной кадр под наведением обязан совпасть с растром до пикселя, поэтому режется здесь же
        im.save(src.with_suffix(".jpg"), "JPEG", quality=80, optimize=True, progressive=True)
        out = src.with_name(f"{src.stem}-dots.jpg")
        halftone(im).save(out, "JPEG", quality=82, optimize=True, progressive=True)
        print(f"{out.name}: {out.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
