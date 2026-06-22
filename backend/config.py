from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    app_name: str = "AgroSat"
    app_version: str = "0.1.0"
    environment: str = "development"
    secret_key: str = "change_me_in_production"
    debug: bool = True

    # Database
    database_url: str = "postgresql://agrosat:agrosat_secret_2024@localhost:5432/agrosat"
    supabase_database_url: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Sentinel Hub (регистрация: https://www.sentinel-hub.com)
    sentinel_hub_client_id: str = ""
    sentinel_hub_client_secret: str = ""

    # NDVI Settings
    ndvi_drop_warning_threshold: float = 0.15   # 15% падение → предупреждение
    ndvi_drop_critical_threshold: float = 0.25  # 25% падение → критично
    ndvi_fetch_interval_hours: int = 12          # проверка каждые 12 часов

    # Open-Meteo (бесплатный, без API ключа)
    weather_api_url: str = "https://api.open-meteo.com/v1/forecast"

    # Anthropic (Claude AI)
    anthropic_api_key: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_notifications_enabled: bool = False

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
