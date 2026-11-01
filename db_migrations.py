"""Versioned PostgreSQL schema setup, separate from Telegram/inverter startup."""

import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from dotenv import load_dotenv
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
MIGRATION_LOCK_ID = 736409821054  # Stable application-specific advisory lock.


def database_url():
    load_dotenv(ROOT / '.env')
    return URL.create(
        'postgresql+asyncpg',
        username=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD', 'postgres'),
        host=os.getenv('POSTGRES_HOST', 'postgres'),
        port=int(os.getenv('POSTGRES_PORT', '5432')),
        database=os.getenv('POSTGRES_DB', 'deye_bot'),
    )


def migration_config():
    load_dotenv(ROOT / '.env')
    config = Config(str(ROOT / 'alembic.ini'))
    config.attributes['database_url'] = database_url()
    legacy_timezone = os.getenv('LEGACY_DB_TIMEZONE', 'UTC')
    ZoneInfo(legacy_timezone)
    config.attributes['legacy_timezone'] = legacy_timezone
    return config


def _upgrade(connection, config):
    config.attributes['connection'] = connection
    command.upgrade(config, 'head')


def _check_version(connection, config):
    expected = set(ScriptDirectory.from_config(config).get_heads())
    actual = set(MigrationContext.configure(connection).get_current_heads())
    if actual != expected:
        raise RuntimeError(
            'Database schema is not at the application revision. '
            'Run alembic upgrade head before starting with DB_AUTO_MIGRATE=false.'
        )


async def prepare_database():
    """Apply every pending revision atomically, or verify a separately migrated DB."""
    config = migration_config()
    auto = os.getenv('DB_AUTO_MIGRATE', 'true').strip().lower()
    if auto not in {'true', 'false', '1', '0', 'yes', 'no'}:
        raise ValueError('DB_AUTO_MIGRATE must be true or false')
    upgrade = auto in {'true', '1', 'yes'}
    engine = create_async_engine(config.attributes['database_url'], poolclass=NullPool, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_upgrade if upgrade else _check_version, config)
    finally:
        await engine.dispose()
    logger.info('Database schema is at the application revision')
