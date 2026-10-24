"""Проверка initData — подписи, которую Telegram кладёт в Mini App.

Единственная защита API календаря: фронт присылает строку с пользователем и
подписью, сервер пересчитывает HMAC ключом из токена бота. Без проверки любой
с curl записался бы от чужого имени и отменил бы чужие записи.
Перенесено из магазина (tg-shop-bot/api/auth.py), где прошло аудит.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from bot.config import settings

# Час — общепринятый дефолт: хватает на запись без спешки и мало для переигровки
# украденной строки. Telegram срок не задаёт.
TTL = 3600


class InitDataError(Exception):
    """Подпись не сошлась, протухла или в строке нет пользователя."""


def user_id_from(raw: str, *, ttl: int = TTL, now: float | None = None) -> int:
    if not raw:
        raise InitDataError("пустой initData")
    try:
        parsed = parse_qsl(raw, strict_parsing=True)
    except ValueError as exc:
        raise InitDataError("initData не разбирается") from exc
    pairs = dict(parsed)
    # Telegram ключи не повторяет. Повтор — склейка: dict молча оставил бы
    # последнее значение, и проверяли бы одно, а читали другое
    if len(pairs) != len(parsed):
        raise InitDataError("ключ повторяется")

    received = pairs.pop("hash", None)
    if not received:
        raise InitDataError("нет hash")
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    # Ключ — литерал "WebAppData", сообщение — токен; наоборот подпись не сойдётся никогда
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    # compare_digest: обычное == выходит на первом несовпавшем байте, и подпись
    # подбирается посимвольно по времени ответа
    if not hmac.compare_digest(calculated, received):
        raise InitDataError("подпись не сошлась")

    try:
        age = (now or time.time()) - int(pairs.get("auth_date", ""))
    except ValueError as exc:
        raise InitDataError("auth_date не число") from exc
    if age > ttl:
        raise InitDataError("initData протух")

    try:
        user = json.loads(pairs.get("user", ""))
    except json.JSONDecodeError as exc:
        raise InitDataError("нет user") from exc
    uid = user.get("id") if isinstance(user, dict) else None
    if type(uid) is not int:  # bool — подкласс int, True прошёл бы как id 1
        raise InitDataError("у пользователя нет числового id")
    return uid


def from_header(header: str | None) -> str:
    """initData из `Authorization: tma <initData>`.

    Заголовок, а не параметр в URL: адрес оседает в логах прокси вместе с
    именем и id клиента.
    """
    scheme, _, value = (header or "").partition(" ")
    if scheme.lower() != "tma" or not value:
        raise InitDataError("ожидался Authorization: tma <initData>")
    return value


def sign(user: dict, *, auth_date: int | None = None, token: str | None = None) -> str:
    """Подписать initData как Telegram. Для тестов и локального превью — сервер это не зовёт."""
    from urllib.parse import urlencode

    pairs = {"auth_date": str(auth_date or int(time.time())), "user": json.dumps(user, separators=(",", ":"))}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", (token or settings.bot_token).encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)
