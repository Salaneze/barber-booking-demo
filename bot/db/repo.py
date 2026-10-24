"""Все чтения и записи базы. Запись к мастеру создаётся только в create_booking.

Кнопка в чате несёт мастера, услугу и время, но им не верим: callback_data
подделывается за минуту. Свободно ли время, сервер пересчитывает сам под
блокировкой мастера.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import Booking, BookingStatus, Client, DayOff, Master, Service, utcnow
from bot.plural import plural
from bot.slots import free_slots, local_to_utc, utc_to_local


class BookingError(Exception):
    """Отказ с текстом для человека: его показываем как есть."""


@lru_cache(maxsize=1)
def tz() -> ZoneInfo:
    return ZoneInfo(settings.shop_tz)


def local_day(dt: datetime) -> date:
    return utc_to_local(dt, tz()).date()


async def services(session: AsyncSession) -> list[Service]:
    return list(await session.scalars(select(Service).order_by(Service.sort, Service.id)))


async def masters_for(session: AsyncSession, service_id: int) -> list[Master]:
    rows = await session.scalars(select(Master).where(Master.active).order_by(Master.id))
    return [m for m in rows if any(s.id == service_id for s in m.services)]


async def hours_on(session: AsyncSession, master: Master, day: date) -> tuple[int, int] | None:
    off = await session.scalar(select(DayOff.id).where(DayOff.master_id == master.id, DayOff.day == day))
    if off:
        return None
    for h in master.hours:
        if h.weekday == day.weekday():
            return h.start_min, h.end_min
    return None


async def busy(session: AsyncSession, master_id: int, day: date, exclude_id: int | None = None):
    lo, hi = local_to_utc(day, 0, tz()), local_to_utc(day, 24 * 60, tz())
    q = select(Booking.starts_at, Booking.ends_at).where(
        Booking.master_id == master_id,
        Booking.status == BookingStatus.ACTIVE,
        Booking.starts_at < hi,
        Booking.ends_at > lo,
    )
    if exclude_id:
        q = q.where(Booking.id != exclude_id)
    return [(a, b) for a, b in await session.execute(q)]


async def slots_for(session, master: Master, service: Service, day: date, now: datetime,
                    exclude_id: int | None = None) -> list[datetime]:
    return free_slots(
        day, await hours_on(session, master, day), await busy(session, master.id, day, exclude_id),
        service.minutes, now=now, tz=tz(), step=settings.slot_step_minutes, lead=settings.lead_minutes,
    )


async def slots_any(session, service: Service, day: date, now: datetime,
                    master_id: int = 0, exclude_id: int | None = None) -> dict[datetime, list[int]]:
    """Время -> мастера, у которых оно свободно. master_id=0 — любой мастер."""
    masters = await masters_for(session, service.id)
    if master_id:
        masters = [m for m in masters if m.id == master_id]
    out: dict[datetime, list[int]] = {}
    for m in masters:
        for s in await slots_for(session, m, service, day, now, exclude_id):
            out.setdefault(s, []).append(m.id)
    return dict(sorted(out.items()))


async def open_days(session, service: Service, master_id: int, now: datetime,
                    exclude_id: int | None = None) -> list[date]:
    today = local_day(now)
    days = [today + timedelta(days=i) for i in range(settings.days_ahead)]
    return [d for d in days if await slots_any(session, service, d, now, master_id, exclude_id)]


async def active_count(session, user_id: int, now: datetime) -> int:
    return await session.scalar(select(func.count(Booking.id)).where(
        Booking.user_id == user_id, Booking.status == BookingStatus.ACTIVE, Booking.starts_at > now,
    )) or 0


async def create_booking(session: AsyncSession, *, user_id: int, name: str, phone: str,
                         service_id: int, master_id: int, starts_at: datetime,
                         now: datetime | None = None, replace_id: int | None = None) -> Booking:
    """Записывает или переносит (replace_id). master_id=0 — первый свободный мастер."""
    now = now or utcnow()
    service = await session.get(Service, service_id)
    if service is None:
        raise BookingError("Такой услуги больше нет.")

    old = None
    if replace_id:
        old = await _own_cancellable(session, replace_id, user_id, now)
    elif await active_count(session, user_id, now) >= settings.max_active_per_user:
        n = settings.max_active_per_user
        raise BookingError(f"У вас уже {n} {plural(n, 'запись', 'записи', 'записей')} вперёд. "
                           "Чтобы записаться ещё, отмените одну из них в «Мои записи».")

    # Окно записи проверяем здесь: подделанная кнопка иначе бронирует на год вперёд
    if not local_day(now) <= local_day(starts_at) < local_day(now) + timedelta(days=settings.days_ahead):
        raise BookingError("На это время запись не открыта.")
    candidates = await masters_for(session, service_id)
    if master_id:
        candidates = [m for m in candidates if m.id == master_id]
    day = local_day(starts_at)
    for m in candidates:
        await session.execute(update(Master).where(Master.id == m.id).values(lock_ver=Master.lock_ver + 1))
        if starts_at not in await slots_for(session, m, service, day, now, old.id if old else None):
            continue
        if old:
            old.status, old.cancelled_by = BookingStatus.CANCELLED, "client"
        b = Booking(
            user_id=user_id, client_name=name, phone=phone, master_id=m.id, service_id=service.id,
            starts_at=starts_at, ends_at=starts_at + timedelta(minutes=service.minutes),
            # Записался за час — напоминание «завтра у вас стрижка» уже бессмысленно
            reminded_day=starts_at - now <= timedelta(hours=settings.remind_day_hours),
            reminded_soon=starts_at - now <= timedelta(hours=settings.remind_soon_hours),
        )
        session.add(b)
        await session.commit()
        await session.refresh(b, ["master", "service"])
        return b
    await session.rollback()
    raise BookingError("Это время только что заняли. Выберите другое.")


async def _own_cancellable(session, booking_id: int, user_id: int, now: datetime) -> Booking:
    b = await session.get(Booking, booking_id)
    if b is None or b.user_id != user_id or b.status != BookingStatus.ACTIVE or b.starts_at <= now:
        raise BookingError("Эта запись уже неактивна.")
    if b.starts_at - now < timedelta(hours=settings.cancel_hours):
        raise BookingError(f"До визита меньше {settings.cancel_hours} ч — отменить или перенести "
                           f"можно только через администратора{': ' + settings.shop_phone if settings.shop_phone else ''}.")
    return b


async def cancel_by_client(session, booking_id: int, user_id: int, now: datetime | None = None) -> Booking:
    b = await _own_cancellable(session, booking_id, user_id, now or utcnow())
    b.status, b.cancelled_by = BookingStatus.CANCELLED, "client"
    await session.commit()
    return b


async def cancel_by_admin(session, booking_id: int) -> Booking:
    b = await session.get(Booking, booking_id)
    if b is None or b.status != BookingStatus.ACTIVE:
        raise BookingError("Запись уже отменена.")
    b.status, b.cancelled_by = BookingStatus.CANCELLED, "admin"
    await session.commit()
    return b


async def user_bookings(session, user_id: int, now: datetime | None = None) -> list[Booking]:
    return list(await session.scalars(select(Booking).where(
        Booking.user_id == user_id, Booking.status == BookingStatus.ACTIVE, Booking.starts_at > (now or utcnow()),
    ).order_by(Booking.starts_at)))


async def day_schedule(session, day: date) -> list[Booking]:
    lo, hi = local_to_utc(day, 0, tz()), local_to_utc(day, 24 * 60, tz())
    return list(await session.scalars(select(Booking).where(
        Booking.status == BookingStatus.ACTIVE, Booking.starts_at >= lo, Booking.starts_at < hi,
    ).order_by(Booking.master_id, Booking.starts_at)))


async def toggle_day_off(session, master_id: int, day: date) -> bool:
    """Переключает выходной. Возвращает True, если день теперь выходной."""
    off = await session.scalar(select(DayOff).where(DayOff.master_id == master_id, DayOff.day == day))
    if off:
        await session.delete(off)
        await session.commit()
        return False
    # Молча закрыть день с записями — клиенты придут к закрытому креслу
    n = sum(1 for b in await day_schedule(session, day) if b.master_id == master_id)
    if n:
        raise BookingError(f"На этот день у мастера {n} записей. Сначала отмените их.")
    session.add(DayOff(master_id=master_id, day=day))
    await session.commit()
    return True


async def get_client(session, user_id: int) -> Client | None:
    return await session.get(Client, user_id)


async def save_client(session, user_id: int, name: str, phone: str) -> None:
    c = await session.get(Client, user_id)
    if c is None:
        session.add(Client(user_id=user_id, name=name, phone=phone))
    else:
        c.name, c.phone = name, phone
    await session.commit()


async def due_reminders(session, now: datetime | None = None) -> tuple[list[Booking], list[Booking]]:
    now = now or utcnow()
    base = select(Booking).where(Booking.status == BookingStatus.ACTIVE, Booking.starts_at > now)
    day = await session.scalars(base.where(
        ~Booking.reminded_day, Booking.starts_at <= now + timedelta(hours=settings.remind_day_hours)))
    soon = await session.scalars(base.where(
        ~Booking.reminded_soon, Booking.starts_at <= now + timedelta(hours=settings.remind_soon_hours)))
    return list(day), list(soon)
