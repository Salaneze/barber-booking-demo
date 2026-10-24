from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import String, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from bot.config import settings


def engine_args(url: str) -> tuple[str, dict]:
    """Строка подключения из панели хостинга -> то, что примет asyncpg.

    Neon и Render выдают `postgresql://...?sslmode=require`: asyncpg не знает
    ни схемы без драйвера, ни sslmode, ни channel_binding и падает на старте.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if not url.startswith("postgresql"):
        return url, {}
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    sslmode = query.pop("sslmode", "")
    query.pop("channel_binding", None)
    url = urlunsplit(("postgresql+asyncpg", parts.netloc, parts.path, urlencode(query), parts.fragment))
    connect_args = {"ssl": True} if sslmode and sslmode != "disable" else {}
    # Бесплатный Postgres засыпает и рвёт соединения в пуле — без pre_ping
    # первый клиент после паузы получает ошибку вместо записи.
    return url, {"connect_args": connect_args, "pool_pre_ping": True, "pool_recycle": 300}


_url, _kw = engine_args(settings.db_url)
engine = create_async_engine(_url, echo=False, **_kw)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _fit_strings(_mapper, _conn, target) -> None:
    # SQLite хранит строку любой длины, Postgres на String(n) роняет запрос.
    # Имя клиента приходит из чата — режем здесь, а не в каждом обработчике.
    for col in target.__table__.columns:
        if isinstance(col.type, String) and col.type.length:
            value = getattr(target, col.key, None)
            if isinstance(value, str) and len(value) > col.type.length:
                setattr(target, col.key, value[: col.type.length])


event.listen(Base, "before_insert", _fit_strings, propagate=True)
event.listen(Base, "before_update", _fit_strings, propagate=True)


async def init_db(eng=None) -> None:
    # v1: create_all. Появится вторая версия схемы — Alembic.
    from bot.db import models  # noqa: F401

    async with (eng or engine).begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
