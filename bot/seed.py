"""Демо-барбершоп при пустой базе. Мастера и цены вымышленные."""
from sqlalchemy import func, select

from bot.db.models import Master, MasterService, Service, WorkHours

SERVICES = [
    ("Мужская стрижка", 60, 1800),
    ("Стрижка машинкой", 30, 1000),
    ("Борода и усы", 30, 1000),
    ("Стрижка + борода", 90, 2600),
    ("Детская стрижка", 45, 1200),
    ("Королевское бритьё", 45, 1500),
]

# имя, о мастере, номера услуг из SERVICES, рабочие дни (0 — пн), часы
MASTERS = [
    ("Артём", "Старший барбер, 8 лет. Классика и бритьё опасной бритвой", [0, 1, 2, 3, 5], [0, 1, 2, 3, 4], (10, 21)),
    ("Игорь", "Фейды и короткие стрижки", [0, 1, 2, 3, 4], [2, 3, 4, 5, 6], (11, 22)),
    ("Даша", "Длинные мужские стрижки и детские", [0, 1, 4], [0, 1, 4, 5, 6], (10, 20)),
]


async def seed_if_empty(session) -> bool:
    if await session.scalar(select(func.count(Service.id))):
        return False
    svc = [Service(title=t, minutes=m, price=p, sort=i) for i, (t, m, p) in enumerate(SERVICES)]
    session.add_all(svc)
    await session.flush()
    for name, about, svc_idx, days, (h0, h1) in MASTERS:
        m = Master(name=name, about=about)
        session.add(m)
        await session.flush()
        session.add_all(MasterService(master_id=m.id, service_id=svc[i].id) for i in svc_idx)
        session.add_all(WorkHours(master_id=m.id, weekday=d, start_min=h0 * 60, end_min=h1 * 60) for d in days)
    await session.commit()
    return True
