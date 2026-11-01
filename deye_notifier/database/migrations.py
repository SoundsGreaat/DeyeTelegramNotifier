"""Versioned PostgreSQL schema setup, separate from bot startup."""

import logging
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from deye_notifier.config import PROJECT_ROOT, Settings

logger = logging.getLogger(__name__)
ROOT = PROJECT_ROOT
MIGRATION_LOCK_ID = 736409821054  # Stable application-specific advisory lock.


def database_url(settings=None):
    settings = settings or Settings.from_database_env()
    return URL.create(
        "postgresql+asyncpg",
        username=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


def migration_config(settings=None):
    settings = settings or Settings.from_database_env()
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url(settings)
    ZoneInfo(settings.legacy_db_timezone)
    config.attributes["legacy_timezone"] = settings.legacy_db_timezone
    return config


def _upgrade(connection, config):
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


def _check_version(connection, config):
    expected = set(ScriptDirectory.from_config(config).get_heads())
    actual = set(MigrationContext.configure(connection).get_current_heads())
    if actual != expected:
        raise RuntimeError(
            "Database schema is not at the application revision. "
            "Run alembic upgrade head before starting with DB_AUTO_MIGRATE=false."
        )


async def prepare_database(settings=None):
    """Apply every pending revision atomically, or verify a separately migrated DB."""
    settings = settings or Settings.from_database_env()
    config = migration_config(settings)
    engine = create_async_engine(config.attributes["database_url"], poolclass=NullPool, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_upgrade if settings.db_auto_migrate else _check_version, config)
    finally:
        await engine.dispose()
    logger.info("Database schema is at the application revision")
