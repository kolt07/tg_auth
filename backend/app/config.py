from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str
    session_encryption_key: str
    database_url: str
    telegram_api_id: int
    telegram_api_hash: str
    redis_url: str = "redis://redis:6379/0"
    default_phone_region: str = "RU"
    db_pool_size: int = 5
    db_max_overflow: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
