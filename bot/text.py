from datetime import date, datetime

from aiogram.utils.text_decorations import html_decoration

from bot.db import repo
from bot.db.models import Booking
from bot.slots import utc_to_local

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


def esc(value: object) -> str:
    # parse_mode=HTML: имя клиента с «<» иначе роняет отправку, и запись
    # молча не доходит до мастера
    return html_decoration.quote(str(value))


def day_label(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d:%d.%m}"


def when(dt: datetime) -> str:
    loc = utc_to_local(dt, repo.tz())
    return f"{WEEKDAYS[loc.weekday()]}, {loc.day} {MONTHS[loc.month - 1]}, {loc:%H:%M}"


def hhmm(dt: datetime) -> str:
    return f"{utc_to_local(dt, repo.tz()):%H:%M}"


def rub(n: int) -> str:
    return f"{n:,} ₽".replace(",", " ")


def booking_card(b: Booking) -> str:
    return (f"<b>{esc(b.service.title)}</b> · {rub(b.service.price)}\n"
            f"{when(b.starts_at)}\nМастер: {esc(b.master.name)}")
