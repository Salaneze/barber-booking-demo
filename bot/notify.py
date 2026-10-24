import logging

from aiogram import Bot

from bot.config import settings

log = logging.getLogger(__name__)


async def send(bot: Bot, chat_id: int, text: str, **kw) -> bool:
    # Клиент заблокировал бота или админ ни разу не нажал /start — это не повод
    # ронять запись, которая уже в базе
    try:
        await bot.send_message(chat_id, text, **kw)
        return True
    except Exception as e:
        log.warning("не доставлено %s: %s", chat_id, e)
        return False


async def notify_admins(bot: Bot, text: str) -> None:
    for admin in settings.admins:
        await send(bot, admin, text)
