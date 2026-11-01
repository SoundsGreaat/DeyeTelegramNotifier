"""Schedule cache persistence."""

import json

from .connection import db_retry


async def load_schedule_cache(pool, cache_key):
    async def operation():
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT payload FROM schedule_cache WHERE cache_key = $1",
                cache_key,
            )
            return json.loads(row["payload"]) if row else None

    return await db_retry(pool, operation)


async def save_schedule_cache(pool, cache_key, payload):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO schedule_cache (cache_key, payload, updated_at)
                VALUES ($1, $2::jsonb, CURRENT_TIMESTAMP)
                ON CONFLICT (cache_key) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
                """,
                cache_key,
                json.dumps(payload, ensure_ascii=False),
            )

    await db_retry(pool, operation)
