"""Mini App: подпись initData, закрытые пути, календарь против слотов, чужие записи, мусор на входе.

Запуск: .venv\\Scripts\\python.exe webapp_test.py
"""
import asyncio
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = Path(__file__).with_name("webapp_test.db")
DB.unlink(missing_ok=True)
os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN-NOT-REAL"
os.environ["DB_URL"] = f"sqlite+aiosqlite:///{DB.name}"
os.environ["PUBLIC_URL"] = "https://example.test"
os.environ["ADMIN_IDS"] = "1"

from aiogram import Bot, Dispatcher  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from bot import tma, webapp  # noqa: E402
from bot.db import repo  # noqa: E402
from bot.db.base import engine, init_db, session_factory  # noqa: E402
from bot.seed import seed_if_empty  # noqa: E402
from bot.web import build_app  # noqa: E402

OK = FAIL = 0
SENT: list[tuple[int, str]] = []


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
    else:
        FAIL += 1
        print(f"FAIL {name} {extra}")


def auth(uid: int, **kw) -> dict:
    return {"Authorization": "tma " + tma.sign({"id": uid, "first_name": "Тест"}, **kw)}


def signature():
    good = tma.sign({"id": 42, "first_name": "A"})
    check("подпись: своя проходит", tma.user_id_from(good) == 42)

    def refused(raw, name, **kw):
        try:
            tma.user_id_from(raw, **kw)
            check(name, False, "пропущено")
        except tma.InitDataError:
            check(name, True)

    refused(good.replace("hash=", "hash=0"), "подпись: испорченный hash")
    refused(tma.sign({"id": 42}, token="999:OTHER"), "подпись: чужой токен")
    refused(tma.sign({"id": 42}, auth_date=int(time.time()) - 7200), "подпись: протухла")
    refused(tma.sign({"id": True}), "подпись: id=true не пользователь 1")
    refused(tma.sign({"first_name": "без id"}), "подпись: нет id")
    refused(good + "&auth_date=1", "подпись: повтор ключа")
    refused("", "подпись: пусто")
    for h in (None, "", "Bearer x", "tma"):
        try:
            tma.from_header(h)
            check(f"заголовок {h!r}", False)
        except tma.InitDataError:
            check(f"заголовок {h!r}", True)


async def api():
    await init_db()
    async with session_factory() as s:
        await seed_if_empty(s)
    bot = Bot(token="123456:TEST-TOKEN-NOT-REAL")

    async def fake_send(chat_id, text, **_):
        SENT.append((chat_id, text))
    bot.send_message = fake_send
    client = TestClient(TestServer(build_app(bot, Dispatcher())))
    await client.start_server()
    try:
        await pages(client)
        await flow(client)
    finally:
        await client.close()
        await bot.session.close()
        await engine.dispose()


async def pages(c):
    r = await c.get("/app")
    csp = r.headers.get("Content-Security-Policy", "")
    check("/app 200", r.status == 200, r.status)
    check("/app: встраивать может только Telegram Web", "frame-ancestors https://web.telegram.org" in csp, csp)
    check("/app: без X-Frame-Options DENY", "X-Frame-Options" not in r.headers)
    check("/app: запросы только к себе", "connect-src 'self'" in csp)
    r = await c.get("/")
    check("сайт по-прежнему DENY", r.headers.get("X-Frame-Options") == "DENY")
    r = await c.get("/app/app.js")
    check("app.js 200 и js", r.status == 200 and "javascript" in r.headers.get("Content-Type", ""))
    for path in ("/app/index.html", "/app/..%2fbot%2fconfig.py", "/app/app.js.map", "/webapp/app.js"):
        r = await c.get(path)
        check(f"закрыто {path}", r.status == 404, r.status)
    for method, path in (("get", "/api/init"), ("get", "/api/calendar?service=1"), ("get", "/api/my"),
                         ("post", "/api/book"), ("post", "/api/cancel")):
        r = await getattr(c, method)(path)
        check(f"без подписи {path} -> 401", r.status == 401, r.status)
        r = await getattr(c, method)(path, headers=auth(5, auth_date=int(time.time()) - 7200))
        check(f"протухшая подпись {path} -> 401", r.status == 401, r.status)


async def first_slot(c, uid, service, master=0, replace=0, skip=0, from_day=0):
    days = (await (await c.get(f"/api/calendar?service={service}&master={master}&replace={replace}",
                                headers=auth(uid))).json())["days"]
    # from_day=1 — не сегодня: запись ближе CANCEL_HOURS уже не перенести
    for d in days[from_day:]:
        if d["free"]:
            slots = (await (await c.get(f"/api/slots?service={service}&master={master}&day={d['d']}&replace={replace}",
                                         headers=auth(uid))).json())["slots"]
            if len(slots) > skip:
                return d["d"], slots[skip]
    raise AssertionError("свободного времени нет")


async def flow(c):
    A, B = 1001, 1002
    info = await (await c.get("/api/init", headers=auth(A))).json()
    check("init: 6 услуг", len(info["services"]) == 6)
    check("init: окно 21 день", info["days_ahead"] == 21)
    check("init: новый клиент без контактов", info["client"] is None)

    # Выходной календарь берёт отдельным запросом, а слоты — своим: оба обязаны его видеть
    first_days = (await (await c.get("/api/calendar?service=4&master=1", headers=auth(A))).json())["days"]
    off_day = next(d["d"] for d in first_days[1:] if d["free"])
    async with session_factory() as s:
        await repo.toggle_day_off(s, 1, date.fromisoformat(off_day))

    # Календарь обязан совпадать с тем, что покажет экран времени этого дня
    for master in (0, 1):
        days = (await (await c.get(f"/api/calendar?service=4&master={master}", headers=auth(A))).json())["days"]
        check(f"календарь m={master}: все 21 день", len(days) == 21, len(days))
        bad = []
        for d in days:
            slots = (await (await c.get(f"/api/slots?service=4&master={master}&day={d['d']}", headers=auth(A))).json())["slots"]
            if len(slots) != d["free"]:
                bad.append((d["d"], d["free"], len(slots)))
        check(f"календарь m={master} = слоты по дням", not bad, bad[:3])
        if master == 1:
            check("выходной мастера — ноль окон", next(d["free"] for d in days if d["d"] == off_day) == 0)

    day, slot = await first_slot(c, A, 1, from_day=1)
    body = {"service": 1, "master": 0, "at": slot["at"], "name": "Аня <b>", "phone": "8 900 111-22-33"}
    r = await c.post("/api/book", json=body, headers=auth(A))
    data = await r.json()
    check("запись 200", r.status == 200, data)
    bid = data["booking"]["id"]
    check("телефон приведён к +7", SENT and "+79001112233" in SENT[-1][1], SENT[-1:])
    check("подтверждение ушло клиенту в чат", any(u == A and "Вы записаны" in t for u, t in SENT))
    check("имя с тегом экранировано в уведомлении", any("Аня &lt;b&gt;" in t for _, t in SENT))
    info = await (await c.get("/api/init", headers=auth(A))).json()
    check("контакты запомнены", info["client"] == {"name": "Аня <b>", "phone": "+79001112233"})

    # Одно время, один мастер — второй клиент получает отказ, а не двойную запись
    master = data["booking"]["master_id"]
    r = await c.post("/api/book", json={**body, "master": master, "name": "Боря"}, headers=auth(B))
    check("второй на то же время -> 409", r.status == 409, r.status)

    days = {d["d"]: d["free"] for d in (await (await c.get(f"/api/calendar?service=1&master={master}", headers=auth(B))).json())["days"]}
    slots = (await (await c.get(f"/api/slots?service=1&master={master}&day={day}", headers=auth(B))).json())["slots"]
    check("занятое время пропало из слотов", slot["at"] not in [s["at"] for s in slots])
    check("и из счётчика календаря", days[day] == len(slots))
    # Чужой id в replace не должен «освобождать» чужое время — иначе через него видно, что занято
    slots_r = (await (await c.get(f"/api/slots?service=1&master={master}&day={day}&replace={bid}", headers=auth(B))).json())["slots"]
    check("replace чужой записи игнорируется", slot["at"] not in [s["at"] for s in slots_r])

    my_b = (await (await c.get("/api/my", headers=auth(B))).json())["bookings"]
    check("чужие записи не видны", my_b == [])
    r = await c.post("/api/cancel", json={"id": bid}, headers=auth(B))
    check("чужую запись не отменить", r.status == 409, r.status)
    r = await c.post("/api/book", json={**body, "replace": bid, "name": "Боря"}, headers=auth(B))
    check("чужую запись не перенести", r.status == 409, r.status)

    # Перенос своей: старая отменяется, новая на другое время
    _, slot2 = await first_slot(c, A, 1, master, replace=bid, skip=3, from_day=1)
    r = await c.post("/api/book", json={**body, "master": master, "at": slot2["at"], "replace": bid}, headers=auth(A))
    check("перенос 200", r.status == 200, await r.text())
    mine = (await (await c.get("/api/my", headers=auth(A))).json())["bookings"]
    check("после переноса одна запись", len(mine) == 1 and mine[0]["at"] == slot2["at"], mine)

    # Мусор на входе
    far = (datetime.utcnow() + timedelta(days=40)).replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
    cases = [
        ({**body, "service": True}, 400, "service=true"),
        ({**body, "service": -1}, 400, "service<0"),
        ({**body, "master": "x"}, 400, "master не число"),
        ({**body, "at": slot["at"] + "+00:00"}, 400, "время со смещением"),
        ({**body, "at": "завтра"}, 400, "время не дата"),
        ({**body, "at": far}, 409, "за окном записи"),
        ({**body, "name": "  "}, 400, "пустое имя"),
        ({**body, "name": "я" * 65}, 400, "имя длиннее 64"),
        ({**body, "phone": "123"}, 400, "короткий телефон"),
        ({**body, "service": 999}, 409, "нет такой услуги"),
    ]
    for payload, want, name in cases:
        webapp._writes.clear()
        r = await c.post("/api/book", json=payload, headers=auth(B))
        check(f"вход: {name} -> {want}", r.status == want, r.status)
    r = await c.post("/api/book", data="не json", headers={**auth(B), "Content-Type": "application/json"})
    check("тело не JSON -> 400", r.status == 400, r.status)
    r = await c.post("/api/book", json=[1, 2], headers=auth(B))
    check("тело-массив -> 400", r.status == 400, r.status)
    r = await c.get("/api/slots?service=1&day=2026-13-40", headers=auth(B))
    check("кривая дата -> 400", r.status == 400, r.status)
    r = await c.get("/api/calendar?service=%C2%B2", headers=auth(B))
    check("service='²' -> 400, не 500", r.status == 400, r.status)

    # Потолок активных записей и лимит частоты
    webapp._writes.clear()
    C = 1003
    for i in range(3):
        _, s = await first_slot(c, C, 2, skip=i * 2)
        r = await c.post("/api/book", json={**body, "service": 2, "at": s["at"]}, headers=auth(C))
        check(f"запись {i + 1} из 3", r.status == 200, await r.text())
    _, s = await first_slot(c, C, 2, skip=7)
    r = await c.post("/api/book", json={**body, "service": 2, "at": s["at"]}, headers=auth(C))
    check("четвёртая -> 409 потолок", r.status == 409 and "уже 3" in (await r.json())["error"], r.status)
    webapp._writes.clear()
    codes = [(await c.post("/api/cancel", json={"id": 99999}, headers=auth(1004))).status for _ in range(12)]
    check("лимит: после 10 попыток 429", codes[:10] == [409] * 10 and codes[10:] == [429, 429], codes)


def main():
    signature()
    asyncio.run(api())
    DB.unlink(missing_ok=True)
    print(f"OK: {OK}   FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
