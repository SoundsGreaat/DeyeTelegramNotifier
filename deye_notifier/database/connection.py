"""PostgreSQL connection management and retry handling."""

import asyncio
import logging

import asyncpg

logger = logging.getLogger(__name__)


async def get_db_pool(settings):
    """Connect to PostgreSQL, retrying startup while the service comes up."""
    for _ in range(10):
        try:
            return await asyncpg.create_pool(
                user=settings.postgres_user,
                password=settings.postgres_password,
                database=settings.postgres_db,
                host=settings.postgres_host,
                port=settings.postgres_port,
                min_size=1,
                max_size=5,
                max_inactive_connection_lifetime=300,
            )
        except Exception as error:
            logger.warning("Waiting for database... (%s)", error)
            await asyncio.sleep(2)
    raise Exception("Could not connect to database")


async def db_retry(pool, operation, attempts=3):
    """Retry transient or stale PostgreSQL connections."""
    for attempt in range(attempts):
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception:
            if attempt >= attempts - 1:
                raise
            try:
                await pool.expire_connections()
            except Exception:
                pass
            await asyncio.sleep(1 + attempt)
