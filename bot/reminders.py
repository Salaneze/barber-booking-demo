"""Напоминания клиентам: за сутки и за пару часов до визита.

Флаг ставится до отправки и коммитится сразу: упади отправка или процесс
между «отправил» и «отметил», клиент получил бы напоминание дважды, а
пропущенное одно — меньшее зло, чем спам на каждом перезапуске.
"""
import asyncio
import logging

from aiogram import Bot

from bot.config import settings
from bot.db import repo
from bot.db.base import session_factory
from bot.notify import send
from bot.text import booking_card, esc

log = logging.getLogger(__name__)


async def remind_once(bot: Bot) -> int:
    sent = 0
    async with session_factory() as session:
        day, soon = await repo.due_reminders(session)
        for b in soon:
            b.reminded_soon = b.reminded_day = True
        for b in day:
            b.reminded_day = True
        await session.commit()
        for b in soon:
            sent += await send(bot, b.user_id, f"⏰ Скоро ваш визит\n\n{booking_card(b)}\n\n{esc(settings.shop_address)}")
        # «За сутки» тому, у кого и так «скоро», не шлём — одно сообщение вместо двух
        soon_ids = {b.id for b in soon}
        for b in day:
            if b.id not in soon_ids:
                sent += await send(bot, b.user_id, f"Напоминаю о записи\n\n{booking_card(b)}\n\n"
                                                   "Не получается прийти — отмените в «Мои записи», "
                                                   "время освободится для других.")
    return sent


async def loop(bot: Bot) -> None:
    while True:
        try:
            if n := await remind_once(bot):
                log.info("напоминаний отправлено: %s", n)
        except Exception:
            log.exception("цикл напоминаний")
        await asyncio.sleep(settings.reminder_sweep_seconds)
