"""Notification and idempotent report queue persistence."""

from .connection import db_retry


async def enqueue_notification(
    pool,
    message_key,
    voltage=None,
    load=0,
    duration="",
    level=None,
    forecast_action="",
    forecast_text="",
    threshold=None,
):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO notification_queue
                    (message_key, voltage, load_watts, duration, level, forecast_action, forecast_text, threshold)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                message_key,
                voltage,
                load,
                duration,
                level,
                forecast_action,
                forecast_text,
                threshold,
            )

    await db_retry(pool, operation)


async def get_pending_notifications(pool):
    async def operation():
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT id, message_key, voltage, load_watts, duration, level,
                       forecast_action, forecast_text, threshold, report_payload
                FROM notification_queue
                WHERE delivered_at IS NULL
                ORDER BY id
                LIMIT 20
                """
            )

    return await db_retry(pool, operation)


async def mark_notification_delivered(pool, event_id):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE notification_queue SET delivered_at = CURRENT_TIMESTAMP WHERE id = $1",
                event_id,
            )

    await db_retry(pool, operation)


async def report_exists(pool, report_key) -> bool:
    async def operation():
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM notification_queue WHERE report_key = $1)", report_key
            )

    return await db_retry(pool, operation)


async def enqueue_report(pool, report_key, payload: str) -> None:
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO notification_queue (message_key, report_key, report_payload)
                VALUES ('statistics', $1, $2::jsonb)
                ON CONFLICT (report_key) WHERE report_key IS NOT NULL DO NOTHING
                """,
                report_key,
                payload,
            )

    await db_retry(pool, operation)
