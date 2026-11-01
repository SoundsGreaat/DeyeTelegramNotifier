"""Monitoring state and transition persistence."""

from .connection import db_retry


async def load_monitor_state(pool):
    async def operation():
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT is_grid_online, last_status_time FROM monitor_state WHERE id = TRUE"
            )

    return await db_retry(pool, operation)


async def save_initial_monitor_state(pool, is_grid_online, status_time):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO monitor_state (id, is_grid_online, last_status_time)
                VALUES (TRUE, $1, $2)
                ON CONFLICT (id) DO UPDATE SET
                    is_grid_online = EXCLUDED.is_grid_online,
                    last_status_time = EXCLUDED.last_status_time
                """,
                is_grid_online,
                status_time,
            )

    await db_retry(pool, operation)


async def record_transition(
    pool,
    is_grid_online,
    status_time,
    voltage,
    load,
    duration,
    forecast_action="",
    forecast_text="",
    battery_soc=None,
    runtime_eta_seconds=None,
    battery_cutoff_soc=None,
):
    """Persist a state change and notification before Telegram delivery."""

    async def operation():
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO notification_queue
                        (message_key, voltage, load_watts, duration, forecast_action, forecast_text, level,
                         runtime_eta_seconds, battery_cutoff_soc)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    "power_restored" if is_grid_online else "warning_battery",
                    voltage,
                    load,
                    duration,
                    forecast_action,
                    forecast_text,
                    battery_soc,
                    runtime_eta_seconds,
                    battery_cutoff_soc,
                )
                await conn.execute(
                    """
                    INSERT INTO monitor_state (id, is_grid_online, last_status_time)
                    VALUES (TRUE, $1, $2)
                    ON CONFLICT (id) DO UPDATE SET
                        is_grid_online = EXCLUDED.is_grid_online,
                        last_status_time = EXCLUDED.last_status_time
                    """,
                    is_grid_online,
                    status_time,
                )

    await db_retry(pool, operation)
