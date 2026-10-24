"""Админка в боте: расписание на день, отмена записи, выходные мастеров."""
from __future__ import annotations

from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db import repo
from bot.db.models import Booking, DayOff, Master, utcnow
from bot.handlers.client import btn, chunks, edit, from_d, kb, to_d
from bot.notify import send
from bot.text import booking_card, day_label, esc, hhmm

router = Router()
# Фильтр на весь роутер: забытая проверка в одном обработчике не откроет
# чужому отмену записей
router.message.filter(F.from_user.id.in_(settings.admins))
router.callback_query.filter(F.from_user.id.in_(settings.admins))


class Adm(CallbackData, prefix="a"):
    act: str
    d: int = 0
    id: int = 0


def home_kb():
    today = repo.local_day(utcnow())
    days = [btn(("Сегодня", "Завтра", "Послезавтра")[i], Adm(act="day", d=to_d(today + timedelta(days=i))))
            for i in range(3)]
    return kb(days, [btn("🚫 Выходные мастеров", Adm(act="offs"))])


@router.message(Command("admin"))
async def admin(msg: Message) -> None:
    await msg.answer("Админка", reply_markup=home_kb())


@router.callback_query(Adm.filter(F.act == "home"))
async def home(cb: CallbackQuery) -> None:
    await edit(cb, "Админка", home_kb())


@router.callback_query(Adm.filter(F.act == "day"))
async def day(cb: CallbackQuery, callback_data: Adm, session: AsyncSession) -> None:
    d = from_d(callback_data.d)
    items = await repo.day_schedule(session, d)
    if not items:
        return await edit(cb, f"{day_label(d)}: записей нет.", kb([btn("« Назад", Adm(act="home"))]))
    lines, cur = [], None
    for b in items:
        if b.master.name != cur:
            cur = b.master.name
            lines.append(f"\n<b>{esc(cur)}</b>")
        lines.append(f"{hhmm(b.starts_at)}-{hhmm(b.ends_at)} {esc(b.service.title)} · "
                     f"{esc(b.client_name)} {esc(b.phone)} (#{b.id})")
    rows = chunks([btn(f"✖ #{b.id} {hhmm(b.starts_at)}", Adm(act="cancel", d=callback_data.d, id=b.id))
                   for b in items], 3)
    await edit(cb, f"<b>{day_label(d)}</b>, записей: {len(items)}\n" + "\n".join(lines),
               kb(*rows, [btn("« Назад", Adm(act="home"))]))


@router.callback_query(Adm.filter(F.act == "cancel"))
async def cancel_ask(cb: CallbackQuery, callback_data: Adm, session: AsyncSession) -> None:
    b = await session.get(Booking, callback_data.id)
    if b is None:
        return await cb.answer("Нет такой записи", show_alert=True)
    await edit(cb, f"Отменить запись #{b.id}? Клиенту придёт сообщение.\n\n{booking_card(b)}\n"
                   f"{esc(b.client_name)}, {esc(b.phone)}",
               kb([btn("Да, отменить", Adm(act="cancel_yes", d=callback_data.d, id=b.id))],
                  [btn("« Нет", Adm(act="day", d=callback_data.d))]))


@router.callback_query(Adm.filter(F.act == "cancel_yes"))
async def cancel_yes(cb: CallbackQuery, callback_data: Adm, session: AsyncSession, bot: Bot) -> None:
    try:
        b = await repo.cancel_by_admin(session, callback_data.id)
    except repo.BookingError as e:
        return await cb.answer(str(e), show_alert=True)
    phone = f" Вопросы — {settings.shop_phone}." if settings.shop_phone else ""
    delivered = await send(bot, b.user_id, f"К сожалению, запись отменена салоном.\n\n{booking_card(b)}\n\n"
                                           f"Выберите другое время: /start.{phone}")
    note = "" if delivered else "\n⚠️ Клиенту не доставлено — позвоните ему."
    await edit(cb, f"Запись #{b.id} отменена.{note}", kb([btn("« К расписанию", Adm(act="day", d=callback_data.d))]))


@router.callback_query(Adm.filter(F.act == "offs"))
async def offs(cb: CallbackQuery, session: AsyncSession) -> None:
    masters = list(await session.scalars(select(Master).where(Master.active).order_by(Master.id)))
    rows = [[btn(m.name, Adm(act="offs_m", id=m.id))] for m in masters]
    await edit(cb, "Чей график поменять?", kb(*rows, [btn("« Назад", Adm(act="home"))]))


@router.callback_query(Adm.filter(F.act.in_({"offs_m", "toggle"})))
async def offs_master(cb: CallbackQuery, callback_data: Adm, session: AsyncSession) -> None:
    m = await session.get(Master, callback_data.id)
    if m is None:
        return await cb.answer("Нет такого мастера", show_alert=True)
    note = ""
    if callback_data.act == "toggle":
        try:
            off = await repo.toggle_day_off(session, m.id, from_d(callback_data.d))
            note = f"\n{day_label(from_d(callback_data.d))}: {'выходной' if off else 'рабочий'}."
        except repo.BookingError as e:
            note = f"\n⚠️ {esc(e)}"
    today = repo.local_day(utcnow())
    days = [today + timedelta(days=i) for i in range(14)]
    offs_ = set(await session.scalars(select(DayOff.day).where(DayOff.master_id == m.id, DayOff.day >= today)))
    work = {h.weekday for h in m.hours}
    buttons = []
    for d in days:
        mark = "🚫" if d in offs_ or d.weekday() not in work else "✅"
        buttons.append(btn(f"{mark} {day_label(d)}", Adm(act="toggle", d=to_d(d), id=m.id)))
    await edit(cb, f"<b>{esc(m.name)}</b>: ✅ работает, 🚫 выходной. Нажмите день, чтобы сделать "
                   f"его выходным или вернуть.\nДни вне обычного графика здесь не открываются.{note}",
               kb(*chunks(buttons, 3), [btn("« Назад", Adm(act="offs"))]))
