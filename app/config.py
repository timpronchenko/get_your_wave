"""Конфигурация приложения через Pydantic Settings."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Всегда читаем .env из корня проекта (рядом с папкой app/), независимо от cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Настройки приложения из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str
    spotify_client_id: str
    spotify_redirect_uri: str = "http://127.0.0.1:8000/callback"
    base_url: str = "http://127.0.0.1:8000"
    deepseek_api_key: str = ""


settings = Settings()
