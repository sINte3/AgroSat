import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings
from functools import lru_cache

from services.sentinel_provider import normalize_sentinel_provider

BACKEND_DIR = Path(__file__).resolve().parent
RUNTIME_ENV_FILE_VARIABLE = "AGROSAT_RUNTIME_ENV_FILE"


def _resolve_runtime_env_file() -> Path:
    explicit_path = os.environ.get(RUNTIME_ENV_FILE_VARIABLE)
    if explicit_path is None:
        return BACKEND_DIR / ".env"

    candidate = Path(explicit_path)
    if not candidate.is_absolute():
        raise RuntimeError("Runtime configuration file path must be absolute.")

    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise RuntimeError("Runtime configuration file is unavailable.") from None

    if not resolved.is_file():
        raise RuntimeError("Runtime configuration file is not a regular file.")
    return resolved


RUNTIME_ENV_FILE = _resolve_runtime_env_file()

# Lowercase known weak / placeholder keys that must never be accepted.
_FORBIDDEN_SECRETS = frozenset({
    "changeme",
    "change_me",
    "replace_me",
    "secret",
    "default",
    "test_secret",
})


class Settings(BaseSettings):
    # App
    app_name: str = "AgroSat"
    app_version: str = "0.1.0"
    release_revision: str = "unknown"
    environment: str = "development"
    secret_key: str = ""
    debug: bool = True
    public_registration_enabled: bool = False
    # First-pilot boundary: Wialon stays disabled until the separately approved
    # Integrated Operations pilot enables both the backend and frontend flags.
    wialon_enabled: bool = False

    # Database
    database_url: str = ""
    supabase_database_url: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Operational state
    collector_status_directory: str = ""
    collector_stale_after_seconds: int = 129600

    # Sentinel Hub (регистрация: https://www.sentinel-hub.com)
    sentinel_hub_provider: str = "planet"
    sentinel_hub_client_id: str = ""
    sentinel_hub_client_secret: str = ""

    @field_validator("sentinel_hub_provider", mode="before")
    @classmethod
    def validate_sentinel_hub_provider(cls, value: object) -> str:
        return normalize_sentinel_provider(value)

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
        env_file = RUNTIME_ENV_FILE
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# The .env.example placeholder value at the time of writing.
_ENV_EXAMPLE_PLACEHOLDER = "замените_на_длинную_случайную_строку_минимум_32_символа"


def _reject_known_insecure(key: str, stripped: str) -> bool:
    """Return True when the key matches any known insecure value."""
    # Exact match against the .env.example cyrillic placeholder.
    if stripped == _ENV_EXAMPLE_PLACEHOLDER:
        return True
    # Project-specific known defaults.
    if stripped == "change_me_in_production":
        return True
    if stripped == "agrosat_dev_secret_key_change_in_prod":
        return True
    # Generic forbidden placeholders (case-insensitive).
    if stripped in _FORBIDDEN_SECRETS:
        return True
    return False


def validate_runtime_security() -> None:
    """Validate that the runtime secret key meets security requirements.

    Must be called before any JWT encode or decode operation, and at
    application startup.  Raises RuntimeError when the key is insecure.
    """
    key = settings.secret_key

    # Reject empty or whitespace-only.
    if not key or not key.strip():
        raise RuntimeError(
            "SECRET_KEY is empty. Set a random value of at least 32 characters."
        )

    normalized = key.strip()

    # Reject keys with surrounding whitespace — do not silently trim.
    if key != normalized:
        raise RuntimeError(
            "SECRET_KEY contains leading or trailing whitespace. "
            "Remove surrounding whitespace and retry."
        )

    lower = normalized.lower()

    # Reject known insecure values *before* the length check so that e.g.
    # "change_me_in_production" (24 characters) is caught by the right message.
    if _reject_known_insecure(key, lower):
        raise RuntimeError(
            "SECRET_KEY contains a known insecure placeholder. "
            "Set a unique random value of at least 32 characters."
        )

    # Reject short keys — check against normalized (stripped) value.
    if len(normalized) < 32:
        raise RuntimeError(
            "SECRET_KEY is too short. Must be at least 32 characters."
        )

    # Key is acceptable.
