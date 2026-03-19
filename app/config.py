"""Конфигурация приложения через Pydantic Settings."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Настройки приложения из переменных окружения."""
    
    telegram_bot_token: str
    spotify_client_id: str
    spotify_redirect_uri: str = "http://127.0.0.1:8000/callback"
    base_url: str = "http://127.0.0.1:8000"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
