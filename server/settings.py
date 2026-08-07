from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MONITOR_", extra="ignore")

    api_key: str = "300664_mymonitorlongapikey"
    database_url: str = "sqlite:///./data/monitor.db"
    telegram_bot_token: str = "8855552265:AAFqhSzXM_8Z9Bj72DAfoVu1NN5o8lwIEW8"
    telegram_chat_id: str = "8655503365"
    alert_cooldown_minutes: int = 60
    rules_file: str = "server/rules.yaml"


settings = Settings()
