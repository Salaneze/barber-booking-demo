"""Расчёт свободного времени — без базы и без Telegram, чтобы гонять тестами.

Всё на входе и выходе — наивное UTC, кроме дня и часов работы: они местные,
так их задаёт владелец («с 10 до 21»). Перевод делается здесь, в одном месте.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def local_to_utc(day: date, minutes: int, tz: ZoneInfo) -> datetime:
    local = datetime.combine(day, time()) + timedelta(minutes=minutes)
    return local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def utc_to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)


def free_slots(
    day: date,
    hours: tuple[int, int] | None,
    busy: list[tuple[datetime, datetime]],
    duration: int,
    *,
    now: datetime,
    tz: ZoneInfo,
    step: int = 30,
    lead: int = 60,
) -> list[datetime]:
    """Начала, куда влезает услуга длиной duration минут.

    hours — рабочие часы мастера в этот день в минутах от полуночи, None —
    выходной. busy — занятые интервалы [начало, конец).
    """
    if hours is None or duration <= 0:
        return []
    start, end = local_to_utc(day, hours[0], tz), local_to_utc(day, hours[1], tz)
    dur = timedelta(minutes=duration)
    earliest = now + timedelta(minutes=lead)

    candidates = set()
    t = start
    while t + dur <= end:
        candidates.add(t)
        t += timedelta(minutes=step)
    # Конец чужой записи — тоже кандидат: после стрижки на 45 минут иначе
    # следующее начало только через полчаса, и в каждом окне теряется четверть часа
    candidates.update(b_end for _, b_end in busy if start <= b_end and b_end + dur <= end)

    free = []
    for s in sorted(candidates):
        e = s + dur
        if s < earliest:
            continue
        if any(s < b_end and b_start < e for b_start, b_end in busy):
            continue
        free.append(s)
    return free
