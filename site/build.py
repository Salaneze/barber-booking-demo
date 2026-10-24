"""Собирает site/index.html из template.html, сида и настроек бота.

Прайс и мастера живут в bot/seed.py, сроки записи — в bot/config.py: копия
на сайте разъехалась бы с ботом на первой же правке цены.

    python site/build.py [--bot britvaa_demo_bot] [--city Москва]
"""
import argparse
import os
import re
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
sys.path.insert(0, str(ROOT))
# Settings требует токен, а сборке он не нужен; настоящий из .env всё равно перекроет
os.environ.setdefault("BOT_TOKEN", "build")
os.chdir(ROOT)

from bot.config import settings  # noqa: E402
from bot.seed import MASTERS, SERVICES  # noqa: E402

DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
# Сущностью, а не символом: литерал U+00A0 в диффе невидим
NBSP = "&nbsp;"


def rub(n: int) -> str:
    return f"{n:,}".replace(",", NBSP) + NBSP + "₽"


def plural(n: int, one: str, few: str, many: str) -> str:
    # Сроки берутся из настроек салона: «за 1 часа» на сайте выглядело бы как опечатка
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n}{NBSP}{word}"


def service_url(bot: str, idx: int) -> str:
    # Сид заливает пустую базу по порядку SERVICES, поэтому id = позиция + 1
    return f"https://t.me/{bot}?start=s{idx + 1}"


def hours_line() -> str:
    spans = {}
    for _, _, _, days, (h0, h1) in MASTERS:
        for d in days:
            lo, hi = spans.get(d, (h0, h1))
            spans[d] = (min(lo, h0), max(hi, h1))
    # Соседние дни с одинаковыми часами склеиваются: «Ср–Вс 10:00–22:00»
    groups = []
    for d in range(7):
        if d not in spans:
            continue
        if groups and groups[-1][1] == d - 1 and groups[-1][2] == spans[d]:
            groups[-1][1] = d
        else:
            groups.append([d, d, spans[d]])
    if len(groups) == 1 and groups[0][:2] == [0, 6]:
        h0, h1 = groups[0][2]
        return f"Ежедневно {h0}:00–{h1}:00"
    parts = []
    for a, b, (h0, h1) in groups:
        days = DAYS[a] if a == b else f"{DAYS[a]}–{DAYS[b]}"
        parts.append(f"{days} {h0}:00–{h1}:00")
    return ", ".join(parts)


def img(name: str, alt: str, w: int, h: int, eager: bool = False) -> str:
    # Пока снимка нет — честная рамка вместо битой картинки
    if not (SITE / "img" / name).exists():
        return f'<div class="ph">фото · {escape(alt)}</div>'
    loading = 'fetchpriority="high"' if eager else 'loading="lazy"'
    return f'<img src="img/{name}" alt="{escape(alt)}" width="{w}" height="{h}" {loading} decoding="async">'


def chat(shave: int) -> str:
    """Макет переписки. Фразы повторяют bot/handlers/client.py дословно."""
    title, minutes, price = SERVICES[shave]
    master = next(m[0] for m in MASTERS if shave in m[2])
    days = ["Пн 26.10", "Вт 27.10", "Ср 28.10", "Чт 29.10", "Пт 30.10", "Сб 31.10"]
    times = ["17:00", "17:30", "18:00", "18:30", "19:00", "19:30"]

    def keys(rows: list[list[str]], on: str = "") -> str:
        out = "".join("<div>" + "".join(f'<span class="on">{k}</span>' if k == on else f"<span>{k}</span>"
                                        for k in r) + "</div>" for r in rows)
        return f'        <div class="keys" aria-hidden="true">{out}</div>'

    t = escape(title)
    return "\n".join([
        '      <div class="chat">',
        f'        <div class="chat-head"><span class="chat-ava">Б</span><div><b>{escape(settings.shop_name)}</b>'
        '<small>бот</small></div></div>',
        f'        <p class="msg"><b>{t}</b>\nВыберите день:</p>',
        keys([days[:3], days[3:]], on="Пт 30.10"),
        f'        <p class="msg"><b>{t}</b> · Пт 30.10\nВыберите время:</p>',
        keys([times[:4], times[4:]], on="18:30"),
        f'        <p class="msg">✅ Вы записаны!\n\n<b>{t}</b> · {rub(price)}\nПт, 30 октября, 18:30\n'
        f'Мастер: {escape(master)}\n\n{escape(settings.shop_address)}\n'
        f'Напомню за день и за {plural(settings.remind_soon_hours, "час", "часа", "часов")}.</p>',
        '      </div>',
    ])


# Файл, подпись, мастер, номер услуги в SERVICES. Мастер должен эту услугу делать —
# иначе подпись «у Даши» ведёт в бота, где Даши в списке нет
CUTS = [
    ("cut-fade", "Фейд", "Игорь", 0),
    ("cut-classic", "Классика с пробором", "Артём", 0),
    ("cut-buzz", "Под машинку", "Игорь", 1),
    ("cut-long", "Длинные", "Даша", 0),
]


def schedule() -> str:
    head = "".join(f'<th scope="col" data-d="{d}">{DAYS[d]}</th>' for d in range(7))
    rows = []
    for name, _, _, days, (h0, h1) in MASTERS:
        cells = "".join(
            f'<td data-d="{d}">{h0}–{h1}</td>' if d in days else f'<td data-d="{d}" class="off">выходной</td>'
            for d in range(7))
        rows.append(f'        <tr><th scope="row">{escape(name)}</th>{cells}</tr>')
    return (f'      <table class="sched">\n        <thead><tr><td></td>{head}</tr></thead>\n'
            f'        <tbody>\n' + "\n".join(rows) + '\n        </tbody>\n      </table>')


def gallery(bot: str) -> str:
    out = []
    for i, (slug, title, master, svc) in enumerate(CUTS):
        assert svc in next(m[2] for m in MASTERS if m[0] == master), f"{master} не делает {SERVICES[svc][0]}"
        if not (SITE / "img" / f"{slug}-dots.jpg").exists():
            continue
        alt = f"{title}, работа мастера {master}"
        out.append(
            f'      <a class="cut" href="{service_url(bot, svc)}" data-reveal style="--i:{i}">\n'
            f'        <span class="cut-img">'
            f'<img src="img/{slug}-dots.jpg" alt="{escape(alt)}" width="800" height="1000" loading="lazy" decoding="async">'
            f'<img class="cut-color" src="img/{slug}.jpg" alt="" width="800" height="1000" loading="lazy" decoding="async">'
            f'</span>\n'
            f'        <span class="cut-cap"><b>{escape(title)}</b><span>{escape(master)} · {escape(SERVICES[svc][0].lower())}</span></span>\n'
            f'      </a>')
    return "\n".join(out)


def faq() -> list[tuple[str, str]]:
    kids = [m[0] for m in MASTERS if any("детск" in SERVICES[i][0].lower() for i in m[2])]
    no_tg = (f"Позвоните: {escape(settings.shop_phone)}. Но в боте видно живое свободное время, по телефону — нет."
             if settings.shop_phone else
             "Пока только через бота: он видит живое расписание мастеров, и занятое время не предложит.")
    items = [
        ("Нужно ли что-то устанавливать?", "Нет. Бот открывается в том Телеграме, что уже стоит в телефоне. Регистрации тоже нет — нужно только имя и номер."),
        ("На сколько вперёд можно записаться?", f"На {plural(settings.days_ahead, 'день', 'дня', 'дней')}. Дальше мастера расписание ещё не открыли."),
        ("Как перенести или отменить?", f"В том же чате, у своей записи. Не позже чем за {plural(settings.cancel_hours, 'час', 'часа', 'часов')} до визита — потом слот уже никто не займёт, и вопрос решает администратор."),
        ("Напомните о записи?", f"Да, дважды: за сутки и за {plural(settings.remind_soon_hours, 'час', 'часа', 'часов')}."),
        ("А без Телеграма?", no_tg),
    ]
    if kids:
        items.append(("Детей стрижёте?", f"Да, детская стрижка — у мастера {escape(kids[0])}." if len(kids) == 1
                      else f"Да, детская стрижка — у мастеров: {escape(', '.join(kids))}."))
    return items


def v2_values(bot: str) -> dict[str, str]:
    q = "\n".join(
        f'      <details>\n        <summary>{escape(a)}<span class="plus" aria-hidden="true"></span></summary>\n'
        f'        <p>{b}</p>\n      </details>' for a, b in faq())
    words = [t for t, _, _ in SERVICES] + ["Без звонков", "Запись в Телеграме"]
    line = "".join(f"<span>{escape(w)}</span><i aria-hidden=\"true\"></i>" for w in words)
    return {
        "GALLERY": gallery(bot),
        "SCHEDULE": schedule(),
        "FAQ": q,
        # Две одинаковые половины: лента сдвигается на −50% и шов не виден
        "MARQUEE": f'<div class="marquee-track"><div>{line}</div><div aria-hidden="true">{line}</div></div>',
    }


def build(bot: str, city: str, template: str = "template.html") -> str:
    bot_url = f"https://t.me/{bot}"
    rows = []
    for i, (title, minutes, price) in enumerate(SERVICES):
        rows.append(
            f'      <li><a class="menu-row" href="{service_url(bot, i)}">'
            f'<span class="menu-title">{escape(title)}</span><span class="menu-leader"></span>'
            f'<span class="menu-min">{minutes}{NBSP}мин</span><span class="menu-price">{rub(price)}</span>'
            f'<span class="menu-go" aria-hidden="true">→</span></a></li>')

    masters = []
    for name, about, svc_idx, _, _ in MASTERS:
        slug = {"Артём": "artem", "Игорь": "igor", "Даша": "dasha"}.get(name, "")
        services = "".join(f"<li>{escape(SERVICES[i][0])}</li>" for i in svc_idx)
        masters.append(
            f'      <article class="master">\n'
            f'        <figure>{img(f"master-{slug}.jpg", f"Барбер {name}", 800, 1000)}</figure>\n'
            f'        <h3>{escape(name)}</h3>\n'
            f'        <p>{escape(about)}</p>\n'
            f'        <ul aria-label="Услуги мастера">{services}</ul>\n'
            f'      </article>')

    shave = next(i for i, s in enumerate(SERVICES) if "бритьё" in s[0].lower())
    phone = (f'      <div><dt>Телефон</dt><dd><a href="tel:{re.sub(r"[^0-9+]", "", settings.shop_phone)}">'
             f'{escape(settings.shop_phone)}</a></dd></div>') if settings.shop_phone else ""
    m = re.search(r"«(.+?)»", settings.shop_name)

    values = {
        "SHOP": escape(settings.shop_name),
        "SHORT": escape(m.group(1) if m else settings.shop_name),
        "CITY": escape(city),
        "ADDRESS": escape(f"{city}, {settings.shop_address}"),
        "HOURS": hours_line(),
        "PHONE": phone,
        "BOT_URL": bot_url,
        "MENU": "\n".join(rows),
        "MASTERS": "\n".join(masters),
        "CHAT": chat(shave),
        "SHAVE_URL": service_url(bot, shave),
        "SHAVE_MIN": str(SERVICES[shave][1]),
        "DAYS": plural(settings.days_ahead, "день", "дня", "дней"),
        "SOON": plural(settings.remind_soon_hours, "час", "часа", "часов"),
        "CANCEL": plural(settings.cancel_hours, "час", "часа", "часов"),
        "IMG_HERO": img("hero.jpg", "Кресло и зеркало в зале барбершопа", 1600, 900, eager=True),
        "IMG_SHAVE": img("detail-shave.jpg", "Королевское бритьё опасной бритвой", 1500, 1000),
    }
    values.update(v2_values(bot))
    html = (SITE / template).read_text(encoding="utf-8")
    html = re.sub(r"\{\{(\w+)\}\}", lambda mt: values[mt.group(1)], html)
    # Пометка строго после doctype: комментарий перед ним включает quirks-режим
    doctype, rest = html.split("\n", 1)
    return f"{doctype}\n<!-- Собрано site/build.py из {template} и bot/seed.py — руками не править -->\n{rest}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default="britvaa_demo_bot")
    ap.add_argument("--city", default="Москва")
    args = ap.parse_args()
    # Основной сайт — светлый v2. Тёмный v1 ни откуда не связан: он для скринов в портфолио
    for tpl, name in (("template-v2.html", "index.html"), ("template.html", "v1.html")):
        out = SITE / name
        out.write_text(build(args.bot, args.city, tpl), encoding="utf-8", newline="\n")
        print(f"собрано: {out}")


if __name__ == "__main__":
    main()
