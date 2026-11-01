"""Alembic CLI and bot startup share the same transaction and migration lock."""

import asyncio
from zoneinfo import ZoneInfo

from alembic import context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from deye_notifier.database.migrations import MIGRATION_LOCK_ID, database_url

config = context.config
legacy_timezone = context.get_x_argument(as_dictionary=True).get(
    "legacy_timezone",
    config.attributes.get("legacy_timezone"),
)
if legacy_timezone is None:
    import os

    # database_url also loads .env for commands invoked without the bot.
    database_url()
    legacy_timezone = os.getenv("LEGACY_DB_TIMEZONE", "UTC")
ZoneInfo(legacy_timezone)
config.attributes["legacy_timezone"] = legacy_timezone


def do_run_migrations(connection):
    # Acquired before Alembic inspects its version table. Two containers cannot
    # both observe the old version and execute the same migration concurrently.
    connection.execute(text("SET LOCAL lock_timeout = '30s'"))
    connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": MIGRATION_LOCK_ID})
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=False)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    engine = create_async_engine(
        config.attributes.get("database_url") or database_url(),
        poolclass=NullPool,
        hide_parameters=True,
    )
    try:
        # All revisions and version records commit together; exceptions roll
        # back DDL, version changes and release the transaction-scoped lock.
        async with engine.begin() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=config.attributes.get("database_url") or database_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    connection = config.attributes.get("connection")
    if connection is not None:
        do_run_migrations(connection)
    else:
        asyncio.run(run_async_migrations())
