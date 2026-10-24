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

    @property
    def admins(self) -> set[int]:
        return {int(x) for x in self.admin_ids.replace(" ", "").split(",") if x}


settings = Settings()
