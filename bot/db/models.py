from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db.base import Base


def utcnow() -> datetime:
    # Наивное UTC: SQLite всё равно отдаёт datetime без пояса, и сравнивать
    # aware с naive в одном процессе — гарантированный TypeError где-нибудь в ночь
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Master(Base):
    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    about: Mapped[str] = mapped_column(String(200), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Счётчик-замок: запись сначала увеличивает его, и вторая запись к тому же
    # мастеру ждёт коммита первой (строчная блокировка в Postgres, блокировка
    # записи в SQLite). Без этого двое, нажавшие «Подтвердить» на одно время
    # одновременно, оба проходят проверку «слот свободен».
    lock_ver: Mapped[int] = mapped_column(Integer, default=0)

    services: Mapped[list["Service"]] = relationship(secondary="master_services", lazy="selectin")
    hours: Mapped[list["WorkHours"]] = relationship(lazy="selectin", cascade="all, delete-orphan")


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(64))
    minutes: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class MasterService(Base):
    __tablename__ = "master_services"

    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), primary_key=True)


class WorkHours(Base):
    """Обычный график мастера: день недели и часы в местном времени."""
    __tablename__ = "work_hours"

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"))
    weekday: Mapped[int] = mapped_column(Integer)  # 0 — понедельник
    start_min: Mapped[int] = mapped_column(Integer)
    end_min: Mapped[int] = mapped_column(Integer)


class DayOff(Base):
    __tablename__ = "days_off"
    __table_args__ = (UniqueConstraint("master_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"))
    day: Mapped[date] = mapped_column(Date)


class Client(Base):
    """Имя и телефон с прошлой записи — второй раз их не спрашиваем."""
    __tablename__ = "clients"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str] = mapped_column(String(20))


class BookingStatus:
    ACTIVE = "active"
    CANCELLED = "cancelled"


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (Index("ix_bookings_master_time", "master_id", "starts_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    client_name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str] = mapped_column(String(20))
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default=BookingStatus.ACTIVE)
    # client | admin — от этого зависит, кому из двоих слать уведомление
    cancelled_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reminded_day: Mapped[bool] = mapped_column(Boolean, default=False)
    reminded_soon: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    master: Mapped[Master] = relationship(lazy="selectin")
    service: Mapped[Service] = relationship(lazy="selectin")
