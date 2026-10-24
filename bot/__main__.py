import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot import reminders, web
from bot.config import settings
from bot.db.base import init_db, session_factory
from bot.handlers import setup_routers
from bot.middlewares import DbSessionMiddleware
from bot.seed import seed_if_empty

log = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    await init_db()
    async with session_factory() as session:
        if await seed_if_empty(session):
            log.info("База была пуста — залил демо-барбершоп")

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    # MemoryStorage держит только ввод имени и телефона: потеря на рестарте
    # стоит клиенту одного повторного ответа, сам выбор едет в кнопках
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(DbSessionMiddleware())
    dp.include_router(setup_routers())

    me = await bot.get_me()
    log.info("Запускаю @%s, админы: %s", me.username, settings.admins or "не заданы")
    task = asyncio.create_task(reminders.loop(bot))
    try:
        if settings.use_webhook:
            await web.run(bot, dp)
            await asyncio.Event().wait()
        else:
            # Локальный polling снимает webhook прода: не запускать при живом деплое
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
    finally:
        task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлен")
