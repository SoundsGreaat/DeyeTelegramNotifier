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
    charge_eta_seconds=None,
    runtime_eta_seconds=None,
    battery_cutoff_soc=None,
    charge_power_watts=None,
):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO notification_queue
                    (message_key, voltage, load_watts, duration, level, forecast_action, forecast_text,
                     threshold, charge_eta_seconds, runtime_eta_seconds, battery_cutoff_soc, charge_power_watts)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                message_key,
                voltage,
                load,
                duration,
                level,
                forecast_action,
                forecast_text,
                threshold,
                charge_eta_seconds,
                runtime_eta_seconds,
                battery_cutoff_soc,
                charge_power_watts,
            )

    await db_retry(pool, operation)


async def get_pending_notifications(pool):
    async def operation():
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT id, message_key, voltage, load_watts, duration, level,
                       forecast_action, forecast_text, threshold, report_payload, charge_eta_seconds,
                       runtime_eta_seconds, battery_cutoff_soc, charge_power_watts
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
