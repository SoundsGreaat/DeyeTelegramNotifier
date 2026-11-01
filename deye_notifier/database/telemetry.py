"""Persistent inverter telemetry sample operations."""

import math
from datetime import datetime, timezone
from numbers import Real
from typing import Any


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _utc(value: datetime) -> datetime:
    return _require_aware(value, "datetime").astimezone(timezone.utc)


async def record_sample(
    pool: Any,
    observed_at: datetime,
    is_grid_online: bool,
    load_watts: int | float,
    battery_soc: int | None,
    *,
    total_load_energy_kwh: float | None = None,
) -> None:
    """Upsert one normalised inverter reading."""
    observed_at = _utc(observed_at)
    if not isinstance(is_grid_online, bool):
        raise TypeError("is_grid_online must be bool")
    if not isinstance(load_watts, Real) or load_watts < 0:
        raise ValueError("load_watts must be a non-negative number")
    load_value = int(round(load_watts))
    if battery_soc is not None and (not isinstance(battery_soc, Real) or not 0 <= battery_soc <= 100):
        raise ValueError("battery_soc must be between 0 and 100")
    if total_load_energy_kwh is not None and (
        not isinstance(total_load_energy_kwh, Real)
        or not math.isfinite(total_load_energy_kwh)
        or total_load_energy_kwh < 0
    ):
        raise ValueError("total_load_energy_kwh must be a non-negative number")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO telemetry_samples (
              observed_at, is_grid_online, load_watts, battery_soc, total_load_energy_kwh
            )
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (observed_at) DO UPDATE SET
              is_grid_online = EXCLUDED.is_grid_online,
              load_watts = EXCLUDED.load_watts,
              battery_soc = EXCLUDED.battery_soc,
              total_load_energy_kwh = EXCLUDED.total_load_energy_kwh
            """,
            observed_at,
            is_grid_online,
            load_value,
            None if battery_soc is None else int(round(battery_soc)),
            None if total_load_energy_kwh is None else float(total_load_energy_kwh),
        )


async def fetch_samples(pool: Any, start: datetime, end: datetime) -> list[Any]:
    """Fetch period samples plus the immediately adjacent boundary samples."""
    start_utc, end_utc = _utc(start), _utc(end)
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            WITH predecessor AS (
                SELECT observed_at, is_grid_online, load_watts, battery_soc, total_load_energy_kwh
                FROM telemetry_samples
                WHERE observed_at < $1
                ORDER BY observed_at DESC
                LIMIT 1
            ), period AS (
                SELECT observed_at, is_grid_online, load_watts, battery_soc, total_load_energy_kwh
                FROM telemetry_samples
                WHERE observed_at >= $1 AND observed_at < $2
            ), successor AS (
                SELECT observed_at, is_grid_online, load_watts, battery_soc, total_load_energy_kwh
                FROM telemetry_samples
                WHERE observed_at >= $2
                ORDER BY observed_at ASC
                LIMIT 1
            )
            SELECT observed_at, is_grid_online, load_watts, battery_soc, total_load_energy_kwh FROM predecessor
            UNION ALL
            SELECT observed_at, is_grid_online, load_watts, battery_soc, total_load_energy_kwh FROM period
            UNION ALL
            SELECT observed_at, is_grid_online, load_watts, battery_soc, total_load_energy_kwh FROM successor
            ORDER BY observed_at
            """,
            start_utc,
            end_utc,
        )
