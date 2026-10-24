"""Ядро записи: расчёт слотов, гонка двух записей на одно время, отмена, перенос.

Запуск: .venv\\Scripts\\python.exe booking_test.py
"""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = Path(__file__).with_name("booking_test.db")
DB.unlink(missing_ok=True)
os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN-NOT-REAL"
os.environ["DB_URL"] = f"sqlite+aiosqlite:///{DB.name}"
os.environ["SHOP_TZ"] = "Europe/Moscow"

from sqlalchemy import select  # noqa: E402

from bot.db import repo  # noqa: E402
from bot.db.base import engine, init_db, session_factory  # noqa: E402
from bot.db.models import Booking, Master, Service  # noqa: E402
from bot.plural import plural  # noqa: E402
from bot.seed import seed_if_empty  # noqa: E402
from bot.slots import free_slots, local_to_utc  # noqa: E402

OK = FAIL = 0
MSK = ZoneInfo("Europe/Moscow")


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
    else:
        FAIL += 1
        print(f"FAIL {name} {extra}")


def plural_forms():
    # 11-14 — исключения, на которых наивное «по последней цифре» и ломается
    got = {n: plural(n, "час", "часа", "часов") for n in (1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 101, 111)}
    want = {1: "час", 2: "часа", 4: "часа", 5: "часов", 11: "часов", 12: "часов", 14: "часов",
            21: "час", 22: "часа", 25: "часов", 101: "час", 111: "часов"}
    check("plural: час/часа/часов", got == want, str({k: v for k, v in got.items() if want[k] != v}))


def slots_pure():
    day = date(2026, 10, 26)  # понедельник
    now = local_to_utc(day, 8 * 60, MSK)
    s = free_slots(day, (10 * 60, 12 * 60), [], 60, now=now, tz=MSK, step=30, lead=60)
    check("сетка 10-12 на час", [x.strftime("%H:%M") for x in s] == ["07:00", "07:30", "08:00"], s)
    check("выходной", free_slots(day, None, [], 60, now=now, tz=MSK) == [])
    # 10:00-10:45 занято: 10:45 должно появиться вне сетки
    b = (local_to_utc(day, 600, MSK), local_to_utc(day, 645, MSK))
    s = free_slots(day, (600, 720), [b], 30, now=now, tz=MSK, step=30, lead=0)
    check("конец чужой записи — кандидат", local_to_utc(day, 645, MSK) in s, s)
    check("пересечение отсечено", local_to_utc(day, 630, MSK) not in s and b[0] not in s, s)
    check("услуга не вылезает за конец дня", local_to_utc(day, 690, MSK) in s and local_to_utc(day, 720, MSK) not in s)
    # запас до начала
    now2 = local_to_utc(day, 600, MSK)
    s = free_slots(day, (600, 720), [], 30, now=now2, tz=MSK, step=30, lead=60)
    check("lead 60 мин", min(s) == local_to_utc(day, 660, MSK), s)
    check("нулевая длительность", free_slots(day, (600, 720), [], 0, now=now, tz=MSK) == [])


async def setup():
    await init_db()
    async with session_factory() as s:
        await seed_if_empty(s)


def monday_10():
    # Ближайший понедельник не раньше чем через 2 дня: Артём работает пн 10-21
    today = datetime.now(MSK).date()
    d = today + timedelta(days=2)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d, local_to_utc(d, 600, MSK)


async def repo_tests():
    d, t10 = monday_10()
    # «сейчас» — за двое суток до визита
    now = local_to_utc(d - timedelta(days=2), 9 * 60, MSK)
    async with session_factory() as s:
        art = await s.scalar(select(Master).where(Master.name == "Артём"))
        haircut = await s.scalar(select(Service).where(Service.title == "Мужская стрижка"))
        kids = await s.scalar(select(Service).where(Service.title == "Детская стрижка"))
        slots = await repo.slots_for(s, art, haircut, d, now)
        check("у Артёма в пн есть 10:00", t10 in slots, slots[:3])

    # Гонка: двое жмут «Подтвердить» на одно время одновременно
    async def book(uid):
        async with session_factory() as s:
            try:
                return await repo.create_booking(s, user_id=uid, name=f"u{uid}", phone="+79000000000",
                                                 service_id=haircut.id, master_id=art.id, starts_at=t10, now=now)
            except repo.BookingError as e:
                return e
    res = await asyncio.gather(book(1), book(2))
    won = [r for r in res if isinstance(r, Booking)]
    check("гонка: ровно одна запись", len(won) == 1, res)
    check("гонка: второй получил отказ", sum(isinstance(r, repo.BookingError) for r in res) == 1)

    async with session_factory() as s:
        n = len([b for b in await repo.day_schedule(s, d) if b.master_id == art.id and b.starts_at == t10])
        check("в базе одна запись на 10:00", n == 1)
        # «Любой мастер» на занятое у Артёма время: Даша в пн тоже работает с 10, но
        # мужскую стрижку делает — должна достаться она
        b = await repo.create_booking(s, user_id=3, name="u3", phone="+7", service_id=haircut.id,
                                      master_id=0, starts_at=t10, now=now)
        check("любой мастер: ушло к свободному", b.master_id != art.id, b.master_id)
        # Детскую стрижку Артём не делает — подделанная кнопка
        try:
            await repo.create_booking(s, user_id=4, name="u4", phone="+7", service_id=kids.id,
                                      master_id=art.id, starts_at=t10 + timedelta(hours=3), now=now)
            check("чужая услуга мастера отклонена", False)
        except repo.BookingError:
            check("чужая услуга мастера отклонена", True)
        # Время не на сетке и не после записи
        try:
            await repo.create_booking(s, user_id=4, name="u4", phone="+7", service_id=haircut.id,
                                      master_id=art.id, starts_at=t10 + timedelta(minutes=137), now=now)
            check("время вне слотов отклонено", False)
        except repo.BookingError:
            check("время вне слотов отклонено", True)
        # Через полгода — окно записи 7 дней
        try:
            await repo.create_booking(s, user_id=4, name="u4", phone="+7", service_id=haircut.id,
                                      master_id=art.id, starts_at=t10 + timedelta(days=182), now=now)
            check("запись за окном отклонена", False)
        except repo.BookingError:
            check("запись за окном отклонена", True)

        # Потолок активных записей на человека
        uid = 5
        made = 0
        for h in (12, 14, 16, 18):
            try:
                await repo.create_booking(s, user_id=uid, name="u5", phone="+7", service_id=haircut.id,
                                          master_id=art.id, starts_at=local_to_utc(d, h * 60, MSK), now=now)
                made += 1
            except repo.BookingError:
                pass
        check("потолок 3 активных", made == 3, made)

        # Перенос: своё же время не мешает сдвигу на полчаса внутри старого интервала
        mine = (await repo.user_bookings(s, 5, now))[0]
        moved = await repo.create_booking(s, user_id=5, name="u5", phone="+7", service_id=haircut.id,
                                          master_id=art.id, starts_at=mine.starts_at + timedelta(minutes=30),
                                          now=now, replace_id=mine.id)
        await s.refresh(mine)
        check("перенос: старая отменена", mine.status == "cancelled")
        check("перенос: новая активна", moved.status == "active")
        check("перенос не упирается в потолок", len(await repo.user_bookings(s, 5, now)) == 3)
        # Чужую запись перенести нельзя
        try:
            await repo.create_booking(s, user_id=6, name="u6", phone="+7", service_id=haircut.id,
                                      master_id=art.id, starts_at=local_to_utc(d, 20 * 60, MSK), now=now,
                                      replace_id=moved.id)
            check("чужую не перенести", False)
        except repo.BookingError:
            check("чужую не перенести", True)

        # Отмена: чужую нельзя, поздно нельзя, вовремя можно
        try:
            await repo.cancel_by_client(s, moved.id, 999, now)
            check("чужую не отменить", False)
        except repo.BookingError:
            check("чужую не отменить", True)
        late = moved.starts_at - timedelta(hours=1)
        try:
            await repo.cancel_by_client(s, moved.id, 5, late)
            check("за час не отменить", False)
        except repo.BookingError as e:
            check("за час не отменить", "меньше" in str(e), e)
        await repo.cancel_by_client(s, moved.id, 5, now)
        slots = await repo.slots_for(s, art, haircut, d, now)
        check("отменённое время снова свободно", moved.starts_at in slots)

        # Выходной на день с записями — отказ, на пустой — ок
        try:
            await repo.toggle_day_off(s, art.id, d)
            check("выходной поверх записей запрещён", False)
        except repo.BookingError:
            check("выходной поверх записей запрещён", True)
        free_day = d + timedelta(days=1)
        check("выходной ставится", await repo.toggle_day_off(s, art.id, free_day) is True)
        check("в выходной слотов нет", await repo.slots_for(s, art, haircut, free_day, now) == [])
        check("выходной снимается", await repo.toggle_day_off(s, art.id, free_day) is False)

        # Напоминания: запись за двое суток — оба флага сброшены; за час до — «скоро»
        day_due, soon_due = await repo.due_reminders(s, t10 - timedelta(hours=23))
        check("за 23 ч — напоминание «за сутки»", any(b.starts_at == t10 for b in day_due))
        check("за 23 ч — ещё не «скоро»", not any(b.starts_at == t10 for b in soon_due))
        _, soon_due = await repo.due_reminders(s, t10 - timedelta(minutes=90))
        check("за 1.5 ч — «скоро»", any(b.starts_at == t10 for b in soon_due))
        # Записался за час до визита — «за сутки» уже не нужно
        close = await repo.create_booking(s, user_id=7, name="u7", phone="+7", service_id=haircut.id,
                                          master_id=art.id, starts_at=local_to_utc(d, 19 * 60, MSK),
                                          now=local_to_utc(d, 17 * 60 + 30, MSK))
        check("поздняя запись: флаги сразу стоят", close.reminded_day and close.reminded_soon)


async def main():
    plural_forms()
    slots_pure()
    await setup()
    await repo_tests()
    await engine.dispose()
    DB.unlink(missing_ok=True)
    print(f"OK: {OK}   FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
