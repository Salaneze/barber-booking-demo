"""Снимает аватар и картинку описания бота «Бритвы» из brand.html.

Рисуем в 2x и ужимаем Pillow-ом до размеров BotFather: текст выходит чище,
чем при съёмке сразу в 1x.
"""
import io
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
PHOTO = (HERE.parent / "site" / "img" / "hero.jpg").resolve().as_uri()

html = (HERE / "brand.html").read_text(encoding="utf-8").replace("PHOTO", PHOTO)
page_file = HERE / "_brand_built.html"
page_file.write_text(html, encoding="utf-8")

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    page = browser.new_page(device_scale_factor=2, viewport={"width": 700, "height": 1100})
    page.goto(page_file.as_uri())
    page.wait_for_selector("body[data-ready='1']", timeout=20000)
    fonts = page.evaluate("[...document.fonts].filter(f => f.status === 'loaded').map(f => f.family)")
    print("шрифты:", sorted(set(fonts)))
    for sel, size, name in (("#avatar", (640, 640), "britva_avatar.jpg"),
                            ("#desc", (640, 360), "britva_description.jpg")):
        png = page.locator(sel).screenshot()
        img = Image.open(io.BytesIO(png)).convert("RGB").resize(size, Image.LANCZOS)
        img.save(HERE / name, quality=92)
        print(name, img.size)
    browser.close()
