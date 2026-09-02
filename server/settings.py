from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MONITOR_", extra="ignore")

    api_key: str = "CHANGE_ME"
    database_url: str = "sqlite:///./data/monitor.db"
    telegram_bot_token: str = "CHANGE_ME"
    telegram_chat_id: str = "CHANGE_ME"
    alert_cooldown_minutes: int = 60
    rules_file: str = "server/rules.yaml"


settings = Settings()
