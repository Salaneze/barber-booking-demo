"""Режим хостинга: что сайт отдаёт наружу, что прячет, и кого пускает webhook.

Запуск: .venv\\Scripts\\python.exe web_test.py
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN-NOT-REAL"
os.environ["PUBLIC_URL"] = "https://example.test"

from aiogram import Bot, Dispatcher  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from bot.config import settings  # noqa: E402
from bot.web import build_app  # noqa: E402

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
    else:
        FAIL += 1
        print(f"FAIL {name} {extra}")


async def main():
    bot = Bot(token=settings.bot_token)
    client = TestClient(TestServer(build_app(bot, Dispatcher())))
    await client.start_server()
    try:
        r = await client.get("/")
        html = await r.text()
        check("главная 200", r.status == 200, r.status)
        check("главная — это v2", 'class="word"' in html)
        check("CSP на странице", "frame-ancestors 'none'" in r.headers.get("Content-Security-Policy", ""))
        # Копия для GitHub Pages: там заголовков нет, работает только meta
        check("CSP в meta", 'http-equiv="Content-Security-Policy"' in html and "connect-src 'none'" in html)
        check("nosniff", r.headers.get("X-Content-Type-Options") == "nosniff")

        r = await client.get("/img/hero.jpg")
        check("картинка 200", r.status == 200, r.status)
        check("у картинки CSP не нужен", "Content-Security-Policy" not in r.headers)

        # Всё, что лежит в site/ рядом со страницей, наружу не должно торчать
        for path in ("/build.py", "/template.html", "/template-v2.html", "/v1.html", "/index.html",
                     "/img/../build.py", "/img/%2e%2e/build.py", "/img/", "/.env", "/img/hero.png"):
            r = await client.get(path)
            check(f"закрыто {path}", r.status == 404, r.status)

        r = await client.get("/healthz")
        check("healthz", r.status == 200 and (await r.json()) == {"ok": True})

        # Webhook: без заголовка и с чужим — отказ; бот при этом не зовётся вовсе
        upd = {"update_id": 1}
        r = await client.post(settings.webhook_path, json=upd)
        check("webhook без секрета", r.status == 401, r.status)
        r = await client.post(settings.webhook_path, json=upd,
                              headers={"X-Telegram-Bot-Api-Secret-Token": "x" * 64})
        check("webhook с чужим секретом", r.status == 401, r.status)
        r = await client.post(settings.webhook_path, json=upd,
                              headers={"X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret})
        check("webhook со своим секретом", r.status == 200, r.status)
        r = await client.post("/tg/" + "0" * 32, json=upd,
                              headers={"X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret})
        check("чужой путь webhook", r.status in (404, 405), r.status)
        check("путь не светит токен", settings.bot_token.split(":")[1] not in settings.webhook_path)
    finally:
        await client.close()
        await bot.session.close()
    print(f"OK: {OK}   FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
