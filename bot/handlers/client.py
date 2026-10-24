"""Запись клиента: услуга -> мастер -> день -> время -> контакты -> подтверждение.

Выбор едет в callback_data от шага к шагу, а не в FSM: кнопки старого
сообщения остаются рабочими, и рестарт бота не теряет недописанную запись.
FSM нужен только на ввод имени и телефона.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           Message, ReplyKeyboardMarkup)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db import repo
from bot.db.models import Booking, Master, Service, utcnow
from bot.notify import notify_admins
from bot.plural import plural
from bot.text import booking_card, day_label, esc, hhmm, rub, when

log = logging.getLogger(__name__)
router = Router()


class Bk(CallbackData, prefix="b"):
    step: str
    s: int = 0   # услуга
    m: int = 0   # мастер, 0 — любой
    d: int = 0   # день, yyyymmdd
    t: int = 0   # начало, минуты от эпохи UTC
    r: int = 0   # запись, которую переносим


class My(CallbackData, prefix="my"):
    act: str
    id: int


class Contact(StatesGroup):
    name = State()
    phone = State()


def btn(text: str, data: CallbackData | str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data if isinstance(data, str) else data.pack())


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def chunks(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


EPOCH = datetime(1970, 1, 1)


def to_t(dt: datetime) -> int:
    return int((dt - EPOCH).total_seconds() // 60)


def from_t(t: int) -> datetime:
    return EPOCH + timedelta(minutes=t)


def to_d(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def from_d(d: int) -> date:
    return date(d // 10000, d // 100 % 100, d % 100)


MENU = kb([btn("✂️ Записаться", "book")], [btn("📅 Мои записи", "my")], [btn("📍 Адрес и контакты", "contacts")])

# Те же три пункта постоянно внизу экрана: меню в сообщении уезжает вверх
# по переписке, а нижние кнопки на месте, сколько бы ни было сообщений
BOOK, MY, ADDR = "✂️ Записаться", "📅 Мои записи", "📍 Адрес"
MAIN = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BOOK)], [KeyboardButton(text=MY), KeyboardButton(text=ADDR)]],
                           resize_keyboard=True, is_persistent=True)


async def edit(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except Exception:
        # «message is not modified» на двойном нажатии и старые сообщения,
        # которые Telegram уже не даёт править, — отвечаем новым
        await cb.message.answer(text, reply_markup=markup)
    await cb.answer()


@router.message(CommandStart())
async def start(msg: Message, command: CommandObject, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    # Кнопка «Записаться» на сайте ведёт сразу на услугу: t.me/<бот>?start=s3
    if command.args and re.fullmatch(r"s\d{1,6}", command.args):
        svc = await session.get(Service, int(command.args[1:]))
        if svc:
            text, markup = await masters_screen(session, Bk(step="mst", s=svc.id))
            await msg.answer(text, reply_markup=markup)
            return
    await msg.answer(f"<b>{esc(settings.shop_name)}</b>\nЗапись к мастеру за минуту — без звонков.\n"
                     "Меню — кнопками внизу.", reply_markup=MAIN)


@router.message(F.text == BOOK)
async def book_btn(msg: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await msg.answer("Выберите услугу:", reply_markup=await services_kb(session))


@router.message(F.text == MY)
async def my_btn(msg: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(*await my_screen(session, msg.from_user.id))


@router.message(F.text == ADDR)
async def addr_btn(msg: Message) -> None:
    await msg.answer(contacts_text())


def contacts_text() -> str:
    phone = f"\n{esc(settings.shop_phone)}" if settings.shop_phone else ""
    return f"<b>{esc(settings.shop_name)}</b>\n{esc(settings.shop_address)}{phone}"


@router.callback_query(F.data == "menu")
async def menu(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit(cb, f"<b>{esc(settings.shop_name)}</b>\nЗапись к мастеру за минуту — без звонков.", MENU)


@router.callback_query(F.data == "contacts")
async def contacts(cb: CallbackQuery) -> None:
    await edit(cb, contacts_text(), kb([btn("« В меню", "menu")]))


async def services_kb(session) -> InlineKeyboardMarkup:
    return kb(*[[btn(f"{s.title} · {s.minutes} мин · {rub(s.price)}", Bk(step="mst", s=s.id))]
                for s in await repo.services(session)])


@router.callback_query(F.data == "book")
async def services_screen(cb: CallbackQuery, session: AsyncSession) -> None:
    await edit(cb, "Выберите услугу:", await services_kb(session))


async def masters_screen(session, c: Bk) -> tuple[str, InlineKeyboardMarkup]:
    masters = await repo.masters_for(session, c.s)
    svc = await session.get(Service, c.s)
    rows = [[btn("Любой свободный мастер", Bk(step="day", s=c.s, m=0, r=c.r))]] if len(masters) > 1 else []
    rows += [[btn(m.name, Bk(step="day", s=c.s, m=m.id, r=c.r))] for m in masters]
    about = "\n".join(f"<b>{esc(m.name)}</b> — {esc(m.about)}" for m in masters)
    back = btn("« Назад", "my" if c.r else "book")
    return f"<b>{esc(svc.title)}</b>\n\n{about}\n\nК кому записать?", kb(*rows, [back])


@router.callback_query(Bk.filter(F.step == "mst"))
async def masters(cb: CallbackQuery, callback_data: Bk, session: AsyncSession) -> None:
    if not await session.get(Service, callback_data.s):
        return await edit(cb, "Такой услуги больше нет.", MENU)
    await edit(cb, *await masters_screen(session, callback_data))


@router.callback_query(Bk.filter(F.step == "day"))
async def days(cb: CallbackQuery, callback_data: Bk, session: AsyncSession) -> None:
    c = callback_data
    svc = await session.get(Service, c.s)
    if not svc:
        return await edit(cb, "Такой услуги больше нет.", MENU)
    open_ = await repo.open_days(session, svc, c.m, utcnow(), c.r or None)
    back = btn("« Назад", Bk(step="mst", s=c.s, r=c.r))
    if not open_:
        n = settings.days_ahead
        return await edit(cb, f"На ближайшие {n} {plural(n, 'день', 'дня', 'дней')} свободного времени нет. "
                              "Попробуйте другого мастера.", kb([back]))
    rows = chunks([btn(day_label(d), Bk(step="time", s=c.s, m=c.m, d=to_d(d), r=c.r)) for d in open_], 3)
    await edit(cb, f"<b>{esc(svc.title)}</b>\nВыберите день:", kb(*rows, [back]))


@router.callback_query(Bk.filter(F.step == "time"))
async def times(cb: CallbackQuery, callback_data: Bk, session: AsyncSession) -> None:
    c = callback_data
    svc = await session.get(Service, c.s)
    if not svc:
        return await edit(cb, "Такой услуги больше нет.", MENU)
    free = await repo.slots_any(session, svc, from_d(c.d), utcnow(), c.m, c.r or None)
    back = btn("« Назад", Bk(step="day", s=c.s, m=c.m, r=c.r))
    if not free:
        return await edit(cb, "На этот день время уже разобрали.", kb([back]))
    rows = chunks([btn(hhmm(t), Bk(step="who", s=c.s, m=c.m, d=c.d, t=to_t(t), r=c.r)) for t in free], 4)
    await edit(cb, f"<b>{esc(svc.title)}</b> · {day_label(from_d(c.d))}\nВыберите время:", kb(*rows, [back]))


async def confirm_screen(session, c: Bk, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    svc = await session.get(Service, c.s)
    master = (await session.get(Master, c.m)).name if c.m else "любой свободный"
    client = await repo.get_client(session, user_id)
    text = (f"Проверьте запись:\n\n<b>{esc(svc.title)}</b> · {svc.minutes} мин · {rub(svc.price)}\n"
            f"{when(from_t(c.t))}\nМастер: {esc(master)}\n"
            f"На имя: {esc(client.name)}, {esc(client.phone)}")
    ok = Bk(step="ok", s=c.s, m=c.m, d=c.d, t=c.t, r=c.r)
    return text, kb([btn("✅ Подтвердить", ok)], [btn("✏️ Другое имя или телефон", "rename")],
                    [btn("« Другое время", Bk(step="time", s=c.s, m=c.m, d=c.d, r=c.r))])


@router.callback_query(Bk.filter(F.step == "who"))
async def who(cb: CallbackQuery, callback_data: Bk, session: AsyncSession, state: FSMContext) -> None:
    await state.update_data(pending=callback_data.pack())
    if await repo.get_client(session, cb.from_user.id):
        return await edit(cb, *await confirm_screen(session, callback_data, cb.from_user.id))
    await ask_name(cb, state)


async def ask_name(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Contact.name)
    await cb.message.answer("Как к вам обращаться?")
    await cb.answer()


@router.callback_query(F.data == "rename")
async def rename(cb: CallbackQuery, state: FSMContext) -> None:
    await ask_name(cb, state)


@router.message(Contact.name, F.text)
async def got_name(msg: Message, state: FSMContext) -> None:
    name = msg.text.strip()
    if not 1 <= len(name) <= 64 or name.startswith("/"):
        await msg.answer("Напишите имя текстом, до 64 символов.")
        return
    await state.update_data(name=name)
    await state.set_state(Contact.phone)
    await msg.answer("Телефон для связи — кнопкой ниже или цифрами:", reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True))


def clean_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    return "+" + digits if 10 <= len(digits) <= 15 else None


@router.message(Contact.phone)
async def got_phone(msg: Message, state: FSMContext, session: AsyncSession) -> None:
    if msg.contact:
        # Через «прикрепить контакт» можно прислать чужой номер — берём только свой
        phone = clean_phone(msg.contact.phone_number) if msg.contact.user_id == msg.from_user.id else None
    else:
        phone = clean_phone(msg.text or "")
    if not phone:
        await msg.answer("Не похоже на номер. Нажмите кнопку или напишите цифрами, например +7 900 123-45-67.")
        return
    data = await state.get_data()
    await repo.save_client(session, msg.from_user.id, data["name"], phone)
    await state.set_state(None)
    # Кнопка «Отправить номер» одноразовая — возвращаем постоянное меню
    await msg.answer("Сохранил.", reply_markup=MAIN)
    pending = data.get("pending")
    if not pending:
        await msg.answer("Что дальше?", reply_markup=MENU)
        return
    text, markup = await confirm_screen(session, Bk.unpack(pending), msg.from_user.id)
    await msg.answer(text, reply_markup=markup)


@router.callback_query(Bk.filter(F.step == "ok"))
async def confirm(cb: CallbackQuery, callback_data: Bk, session: AsyncSession, bot: Bot, state: FSMContext) -> None:
    c = callback_data
    client = await repo.get_client(session, cb.from_user.id)
    if client is None:
        return await ask_name(cb, state)
    try:
        b = await repo.create_booking(session, user_id=cb.from_user.id, name=client.name, phone=client.phone,
                                      service_id=c.s, master_id=c.m, starts_at=from_t(c.t), replace_id=c.r or None)
    except repo.BookingError as e:
        return await edit(cb, esc(e), kb([btn("« Другое время", Bk(step="time", s=c.s, m=c.m, d=c.d, r=c.r))],
                                         [btn("« В меню", "menu")]))
    await state.clear()
    head = "Запись перенесена" if c.r else "Вы записаны"
    soon = settings.remind_soon_hours
    await edit(cb, f"✅ {head}!\n\n{booking_card(b)}\n\n{esc(settings.shop_address)}\n"
                   f"Напомню за день и за {soon} {plural(soon, 'час', 'часа', 'часов')}.")
    what = f"Перенос записи #{c.r} -> #{b.id}" if c.r else f"Новая запись #{b.id}"
    await notify_admins(bot, f"{what}\n\n{booking_card(b)}\nКлиент: {esc(b.client_name)}, {esc(b.phone)}")


async def my_screen(session, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    items = await repo.user_bookings(session, user_id)
    if not items:
        return "Активных записей нет.", kb([btn("✂️ Записаться", "book")])
    rows = [[btn(f"Перенести {hhmm(b.starts_at)}", Bk(step="mst", s=b.service_id, r=b.id)),
             btn("Отменить", My(act="cancel", id=b.id))] for b in items]
    text = "\n\n".join(booking_card(b) for b in items)
    return (f"Ваши записи:\n\n{text}\n\nОтменить или перенести можно не позже чем за "
            f"{settings.cancel_hours} ч до визита.", kb(*rows))


@router.callback_query(F.data == "my")
async def my_bookings(cb: CallbackQuery, session: AsyncSession) -> None:
    await edit(cb, *await my_screen(session, cb.from_user.id))


@router.callback_query(My.filter(F.act == "cancel"))
async def cancel_ask(cb: CallbackQuery, callback_data: My, session: AsyncSession) -> None:
    b = await session.get(Booking, callback_data.id)
    if b is None or b.user_id != cb.from_user.id:
        return await cb.answer("Запись не найдена", show_alert=True)
    await edit(cb, f"Отменить запись?\n\n{booking_card(b)}",
               kb([btn("Да, отменить", My(act="cancel_yes", id=b.id))], [btn("« Нет", "my")]))


@router.callback_query(My.filter(F.act == "cancel_yes"))
async def cancel_yes(cb: CallbackQuery, callback_data: My, session: AsyncSession, bot: Bot) -> None:
    try:
        b = await repo.cancel_by_client(session, callback_data.id, cb.from_user.id)
    except repo.BookingError as e:
        return await edit(cb, esc(e), kb([btn("« Мои записи", "my")]))
    await edit(cb, "Запись отменена.")
    await notify_admins(bot, f"Клиент отменил запись #{b.id}\n\n{booking_card(b)}\n"
                             f"Клиент: {esc(b.client_name)}, {esc(b.phone)}")


@router.message(Command("menu"))
async def menu_cmd(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(f"<b>{esc(settings.shop_name)}</b>", reply_markup=MENU)
