"""Режим хостинга: webhook Telegram и сайт салона в одном aiohttp-процессе."""
import logging
import re
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

from bot.config import settings

log = logging.getLogger(__name__)
SITE = Path(__file__).resolve().parent.parent / "site"

# Сайт статический и без форм, поэтому политика жёсткая. 'unsafe-inline' — цена
# инлайнового <style> и двух маленьких скриптов: ввода от посетителя на странице нет,
# внедрять в неё нечего. Шрифты — Google Fonts, больше внешних источников нет.
CSP = ("default-src 'self'; img-src 'self' data:; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; "
       "connect-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


IMG_NAME = re.compile(r"[a-z0-9-]+\.jpg")


@web.middleware
async def headers(request: web.Request, handler):
    resp = await handler(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


async def index(_: web.Request) -> web.FileResponse:
    # CSP ставится здесь, а не в middleware: у FileResponse тип контента
    # появляется только при отправке, и проверка по нему молча пропускала страницу.
    # no-cache — страница меняется с каждым релизом.
    return web.FileResponse(SITE / "index.html",
                            headers={"Cache-Control": "no-cache", "Content-Security-Policy": CSP})


async def image(request: web.Request) -> web.FileResponse:
    # Белый список вместо add_static: рядом лежат исходники из Gemini по 6 МБ (.png),
    # и отдавать их нельзя, даже если однажды попадут в деплой
    name = request.match_info["name"]
    path = SITE / "img" / name
    if not IMG_NAME.fullmatch(name) or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


async def healthz(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def build_app(bot: Bot, dp: Dispatcher) -> web.Application:
    app = web.Application(middlewares=[headers])
    # Наружу только страница и картинки: build.py, шаблоны и тёмный v1 из site/ не отдаются
    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/img/{name}", image)
    SimpleRequestHandler(dp, bot, secret_token=settings.webhook_secret).register(app, settings.webhook_path)
    return app


async def run(bot: Bot, dp: Dispatcher) -> None:
    if not settings.public_url.startswith("https://"):
        raise SystemExit("USE_WEBHOOK=true, но публичного https-адреса нет: задайте PUBLIC_URL")
    # Очередь не сбрасываем: на засыпающем хостинге первое сообщение и будит
    # контейнер, Telegram ставит его на повтор — drop_pending выкинул бы его молча
    await bot.set_webhook(settings.public_url + settings.webhook_path,
                          secret_token=settings.webhook_secret,
                          allowed_updates=dp.resolve_used_update_types())
    log.info("Режим webhook: %s/tg/***", settings.public_url)
    runner = web.AppRunner(build_app(bot, dp), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, settings.web_host, settings.web_port).start()
    log.info("Сайт и webhook слушают %s:%s", settings.web_host, settings.web_port)
