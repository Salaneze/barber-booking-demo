import hashlib
import hmac
import os

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    admin_ids: str = ""
    db_url: str = "sqlite+aiosqlite:///barber.db"

    shop_name: str = "Барбершоп «Бритва»"
    shop_address: str = "Адрес не задан"
    shop_phone: str = ""
    # Расписание мастеров — в местном времени, в базе всё в UTC
    shop_tz: str = "Europe/Moscow"

    # Шаг сетки начала записи. Свободное время сразу после чужой записи
    # предлагается вне сетки, поэтому 45-минутная стрижка не оставляет дыр.
    slot_step_minutes: int = 30
    # Ближе этого к «сейчас» записаться нельзя: мастер должен успеть увидеть запись
    lead_minutes: int = 60
    days_ahead: int = 7
    # Позже — только через администратора: слот уже никто не займёт
    cancel_hours: int = 3
    # Потолок будущих записей на одного человека. Без него один аккаунт
    # забивает сетку всем мастерам на неделю вперёд за пару минут.
    max_active_per_user: int = 3

    remind_day_hours: int = 24
    remind_soon_hours: int = 2
    reminder_sweep_seconds: int = 60

    # Хостинг: webhook будит заснувший free-контейнер, polling в нём просто глохнет.
    # Тот же процесс отдаёт сайт, так что сайт и бот живут по одному адресу.
    use_webhook: bool = False
    web_host: str = "127.0.0.1"
    # Render сообщает порт через PORT, локально удобнее WEB_PORT в .env
    web_port: int = Field(default=8080, validation_alias=AliasChoices("WEB_PORT", "PORT"))
    public_url_override: str = Field(default="", validation_alias="PUBLIC_URL")

    @property
    def public_url(self) -> str:
        # RENDER_EXTERNAL_URL платформа подставляет сама — домен не дублируется руками
        return (self.public_url_override or os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/")

    @property
    def webhook_path(self) -> str:
        # Из токена: стабилен между перезапусками и не угадывается снаружи
        digest = hmac.new(self.bot_token.encode(), b"webhook-path", hashlib.sha256).hexdigest()
        return f"/tg/{digest[:32]}"

    @property
    def webhook_secret(self) -> str:
        # Вторая проверка: путь может осесть в логах прокси, заголовок — нет
        return hmac.new(self.bot_token.encode(), b"webhook-secret", hashlib.sha256).hexdigest()

    @property
    def admins(self) -> set[int]:
        return {int(x) for x in self.admin_ids.replace(" ", "").split(",") if x}


settings = Settings()
