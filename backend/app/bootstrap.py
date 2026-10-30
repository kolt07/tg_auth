import asyncio
import logging

from sqlalchemy import text

from app.config import Settings, get_settings
from app.db import Base, get_engine
from app.redis_client import create_redis
from app.security import build_fernet

logger = logging.getLogger("tgauth.bootstrap")


def validate_settings(settings: Settings) -> None:
    if len(settings.api_key.strip()) < 8:
        raise RuntimeError("API_KEY має містити щонайменше 8 символів")
    if settings.telegram_api_id < 1:
        raise RuntimeError("TELEGRAM_API_ID має бути додатним числом з my.telegram.org")
    if len(settings.telegram_api_hash.strip()) < 16:
        raise RuntimeError("TELEGRAM_API_HASH має бути з my.telegram.org")
    try:
        build_fernet(settings.session_encryption_key)
    except ValueError as exc:
        raise RuntimeError("SESSION_ENCRYPTION_KEY має бути ключем Fernet") from exc


async def wait_for_db() -> None:
    engine = get_engine()
    last_error = "error"
    for _ in range(30):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as exc:
            last_error = type(exc).__name__
            logger.info("database not ready %s", last_error)
            await asyncio.sleep(1)
    raise RuntimeError(f"База даних недоступна ({last_error})")


async def wait_for_redis() -> None:
    client = create_redis()
    last_error = "error"
    try:
        for _ in range(30):
            try:
                await client.ping()
                return
            except Exception as exc:
                last_error = type(exc).__name__
                logger.info("redis not ready %s", last_error)
                await asyncio.sleep(1)
        raise RuntimeError(f"Redis недоступний ({last_error})")
    finally:
        await client.aclose()


async def create_tables() -> None:
    from app import models  # noqa: F401

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "ALTER TABLE account_sessions "
                "ADD COLUMN IF NOT EXISTS source_bot VARCHAR(64)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_account_sessions_source_bot "
                "ON account_sessions (source_bot)"
            )
        )


async def prepare_runtime() -> Settings:
    settings = get_settings()
    validate_settings(settings)
    await wait_for_db()
    await wait_for_redis()
    await create_tables()
    return settings
