"""Mini App с календарём: раздача фронта из webapp/ и JSON API для него.

Логики записи здесь нет — только разбор запроса и вызов того же repo, что у
чата. Всё, что пришло с фронта, считается подделанным: пользователь берётся
только из подписи initData, слот и окно проверяет create_booking.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from pathlib import Path

from aiogram import Bot
from aiohttp import web

from bot import tma
from bot.config import settings
from bot.db import repo
from bot.db.base import session_factory
from bot.db.models import Booking, Service, utcnow
from bot.handlers.client import clean_phone
from bot.notify import notify_admins, send
from bot.slots import utc_to_local
from bot.text import admin_booked_text, admin_cancelled_text, booked_text, when

log = logging.getLogger(__name__)
FRONT = Path(__file__).resolve().parent.parent / "webapp"
ASSETS = {"app.js": "application/javascript", "app.css": "text/css"}

# Telegram Web (web.telegram.org) открывает Mini App во фрейме — ему встраивать
# можно, остальным нет. Скрипт SDK — с telegram.org, шрифты сайта — с Google
# Fonts, запросы — только к себе
CSP = ("default-src 'self'; script-src 'self' https://telegram.org; "
       "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors https://web.telegram.org; "
       "base-uri 'none'; form-action 'none'")

# Запись и отмена шлют сообщения админам: без потолка один человек
# заваливает их чат, просто дёргая кнопку
WRITE_LIMIT, WRITE_WINDOW = 10, 60.0
_writes: dict[int, deque] = defaultdict(deque)


def _too_many(uid: int) -> bool:
    now = time.monotonic()
    q = _writes[uid]
    while q and now - q[0] > WRITE_WINDOW:
        q.popleft()
    if len(q) >= WRITE_LIMIT:
        return True
    q.append(now)
    return False


_ERRORS = {400: web.HTTPBadRequest, 401: web.HTTPUnauthorized, 404: web.HTTPNotFound,
           409: web.HTTPConflict, 429: web.HTTPTooManyRequests}


def _fail(status: int, text: str) -> web.HTTPException:
    # Текст — для человека: фронт показывает его как есть
    return _ERRORS[status](text=json.dumps({"error": text}, ensure_ascii=False), content_type="application/json")


def _user(request: web.Request) -> int:
    try:
        return tma.user_id_from(tma.from_header(request.headers.get("Authorization")))
    except tma.InitDataError as e:
        # Причину в ответ не отдаём: «подпись не сошлась» против «протух» — подсказка подбору
        log.info("initData отклонён: %s", e)
        raise _fail(401, "Откройте запись через бота в Telegram.")


def _int(value, name: str, *, zero_ok: bool = False) -> int:
    # bool — подкласс int: True из JSON иначе стал бы услугой №1
    # isascii: "²".isdigit() тоже True, а int("²") падает
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    if type(value) is not int or value < 0 or (value == 0 and not zero_ok) or value > 2**31:
        raise _fail(400, f"Неверный параметр {name}.")
    return value


def _service_json(s: Service) -> dict:
    return {"id": s.id, "title": s.title, "minutes": s.minutes, "price": s.price}


def _booking_json(b: Booking) -> dict:
    return {"id": b.id, "service": b.service.title, "price": b.service.price, "master": b.master.name,
            "when": when(b.starts_at), "at": b.starts_at.isoformat(),
            "service_id": b.service_id, "master_id": b.master_id}


async def page(_: web.Request) -> web.FileResponse:
    return web.FileResponse(FRONT / "index.html", headers={"Cache-Control": "no-cache", "Content-Security-Policy": CSP})


async def asset(request: web.Request) -> web.FileResponse:
    name = request.match_info["name"]
    if name not in ASSETS:
        raise web.HTTPNotFound()
    return web.FileResponse(FRONT / name, headers={"Cache-Control": "no-cache", "Content-Type": ASSETS[name]})


async def init(request: web.Request) -> web.Response:
    uid = _user(request)
    async with session_factory() as session:
        services = await repo.services(session)
        masters = {}
        for s in services:
            for m in await repo.masters_for(session, s.id):
                masters.setdefault(m.id, {"id": m.id, "name": m.name, "about": m.about, "services": []})
                masters[m.id]["services"].append(s.id)
        client = await repo.get_client(session, uid)
    return web.json_response({
        "shop": settings.shop_name, "address": settings.shop_address, "phone": settings.shop_phone,
        "days_ahead": settings.days_ahead, "cancel_hours": settings.cancel_hours,
        "today": repo.local_day(utcnow()).isoformat(),
        "services": [_service_json(s) for s in services], "masters": list(masters.values()),
        "client": {"name": client.name, "phone": client.phone} if client else None,
    })


async def _service(session, request: web.Request) -> Service:
    svc = await session.get(Service, _int(request.query.get("service"), "service"))
    if svc is None:
        raise _fail(404, "Такой услуги больше нет.")
    return svc


async def calendar(request: web.Request) -> web.Response:
    uid = _user(request)
    master = _int(request.query.get("master", "0"), "master", zero_ok=True)
    replace = _int(request.query.get("replace", "0"), "replace", zero_ok=True)
    async with session_factory() as session:
        svc = await _service(session, request)
        days = await repo.calendar(session, svc, master, utcnow(), await _own(session, replace, uid))
    return web.json_response({"days": [{"d": d.isoformat(), "free": n} for d, n in days.items()]})


async def slots(request: web.Request) -> web.Response:
    uid = _user(request)
    master = _int(request.query.get("master", "0"), "master", zero_ok=True)
    replace = _int(request.query.get("replace", "0"), "replace", zero_ok=True)
    try:
        day = date.fromisoformat(request.query.get("day", ""))
    except ValueError:
        raise _fail(400, "Неверная дата.")
    now = utcnow()
    today = repo.local_day(now)
    if not today <= day < today + timedelta(days=settings.days_ahead):
        return web.json_response({"slots": []})
    async with session_factory() as session:
        svc = await _service(session, request)
        free = await repo.slots_any(session, svc, day, now, master, await _own(session, replace, uid))
    return web.json_response({"slots": [
        {"t": f"{utc_to_local(t, repo.tz()):%H:%M}", "at": t.isoformat()} for t in free]})


async def _own(session, booking_id: int, uid: int) -> int | None:
    # Чужую запись «исключить из занятых» нельзя: иначе по её id видно, свободно ли её время
    if not booking_id:
        return None
    b = await session.get(Booking, booking_id)
    return booking_id if b is not None and b.user_id == uid else None


async def _body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise _fail(400, "Тело запроса — не JSON.")
    if not isinstance(data, dict):
        raise _fail(400, "Тело запроса — не объект.")
    return data


async def book(request: web.Request) -> web.Response:
    uid = _user(request)
    if _too_many(uid):
        raise _fail(429, "Слишком много попыток. Подождите минуту.")
    data = await _body(request)
    service = _int(data.get("service"), "service")
    master = _int(data.get("master", 0), "master", zero_ok=True)
    replace = _int(data.get("replace", 0), "replace", zero_ok=True)
    try:
        starts_at = datetime.fromisoformat(str(data.get("at", "")))
    except ValueError:
        raise _fail(400, "Неверное время.")
    # Время ходит наивным UTC; со смещением — значит, собрано не нашим фронтом
    if starts_at.tzinfo is not None:
        raise _fail(400, "Неверное время.")
    name = str(data.get("name", "")).strip()
    phone = clean_phone(str(data.get("phone", "")))
    if not 1 <= len(name) <= 64:
        raise _fail(400, "Напишите имя, до 64 символов.")
    if not phone:
        raise _fail(400, "Не похоже на номер. Например: +7 900 123-45-67.")

    bot: Bot = request.app["bot"]
    async with session_factory() as session:
        try:
            b = await repo.create_booking(session, user_id=uid, name=name, phone=phone, service_id=service,
                                          master_id=master, starts_at=starts_at, replace_id=replace or None)
        except repo.BookingError as e:
            raise _fail(409, str(e))
        await repo.save_client(session, uid, name, phone)
    # Подтверждение ещё и в чат: там его видно потом, и оттуда же придут напоминания
    await send(bot, uid, booked_text(b, moved=bool(replace)))
    await notify_admins(bot, admin_booked_text(b, replace or None))
    return web.json_response({"booking": _booking_json(b)})


async def my(request: web.Request) -> web.Response:
    uid = _user(request)
    async with session_factory() as session:
        items = await repo.user_bookings(session, uid)
    return web.json_response({"bookings": [_booking_json(b) for b in items]})


async def cancel(request: web.Request) -> web.Response:
    uid = _user(request)
    if _too_many(uid):
        raise _fail(429, "Слишком много попыток. Подождите минуту.")
    booking_id = _int((await _body(request)).get("id"), "id")
    async with session_factory() as session:
        try:
            b = await repo.cancel_by_client(session, booking_id, uid)
        except repo.BookingError as e:
            raise _fail(409, str(e))
    await notify_admins(request.app["bot"], admin_cancelled_text(b))
    return web.json_response({"ok": True})


def register(app: web.Application) -> None:
    app.router.add_get("/app", page)
    app.router.add_get("/app/{name}", asset)
    app.router.add_get("/api/init", init)
    app.router.add_get("/api/calendar", calendar)
    app.router.add_get("/api/slots", slots)
    app.router.add_post("/api/book", book)
    app.router.add_get("/api/my", my)
    app.router.add_post("/api/cancel", cancel)
