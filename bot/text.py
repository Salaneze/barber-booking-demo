from datetime import date, datetime

from aiogram.utils.text_decorations import html_decoration

from bot.config import settings
from bot.db import repo
from bot.db.models import Booking
from bot.plural import plural
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


# Подтверждение и уведомления админу одни на чат и Mini App: запись из календаря
# должна выглядеть в переписке так же, как сделанная кнопками
def booked_text(b: Booking, moved: bool = False) -> str:
    soon = settings.remind_soon_hours
    head = "Запись перенесена" if moved else "Вы записаны"
    return (f"✅ {head}!\n\n{booking_card(b)}\n\n{esc(settings.shop_address)}\n"
            f"Напомню за день и за {soon} {plural(soon, 'час', 'часа', 'часов')}.")


def admin_booked_text(b: Booking, moved_from: int | None = None) -> str:
    what = f"Перенос записи #{moved_from} -> #{b.id}" if moved_from else f"Новая запись #{b.id}"
    return f"{what}\n\n{booking_card(b)}\nКлиент: {esc(b.client_name)}, {esc(b.phone)}"


def admin_cancelled_text(b: Booking) -> str:
    return f"Клиент отменил запись #{b.id}\n\n{booking_card(b)}\nКлиент: {esc(b.client_name)}, {esc(b.phone)}"
