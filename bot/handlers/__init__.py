from aiogram import Router

from bot.handlers import admin, client


def setup_routers() -> Router:
    root = Router()
    root.include_router(admin.router)
    root.include_router(client.router)
    return root
