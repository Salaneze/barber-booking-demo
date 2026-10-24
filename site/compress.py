"""PNG из Gemini (5-6 МБ) -> JPG для сайта. Исходники остаются рядом, в git не идут.

    python site/compress.py
"""
from pathlib import Path

from PIL import Image

IMG = Path(__file__).resolve().parent / "img"
# Ширина под самый большой показ: hero во весь экран, портреты в колонку трети
WIDTH = {"hero": 2000, "detail-shave": 1600}


def main() -> None:
    for src in sorted(IMG.glob("*.png")):
        if src.stem.startswith("cut-"):
            continue  # кадр 4:5 и растр к нему режет halftone.py
        im = Image.open(src).convert("RGB")
        w = WIDTH.get(src.stem, 900)
        if im.width > w:
            im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
        out = src.with_suffix(".jpg")
        im.save(out, "JPEG", quality=80, optimize=True, progressive=True)
        print(f"{out.name}: {im.width}x{im.height}, {out.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
