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
    grid_voltage: float | None = None,
    grid_connected: bool | None = None,
    battery_current: float | None = None,
    battery_capacity_ah: float | None = None,
    shutdown_soc: float | None = None,
    battery_power_watts: int | None = None,
    battery_voltage: float | None = None,
    battery_temperature_c: float | None = None,
    inverter_dc_temperature_c: float | None = None,
    inverter_radiator_temperature_c: float | None = None,
    runtime_eta_seconds: float | None = None,
    charge_eta_seconds: float | None = None,
    battery_cutoff_soc: float | None = None,
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

    def optional_number(name: str, value: Real | None, minimum: float, maximum: float) -> float | None:
        if value is None:
            return None
        if (
            not isinstance(value, Real)
            or isinstance(value, bool)
            or not math.isfinite(value)
            or not minimum <= value <= maximum
        ):
            raise ValueError(f"{name} must be a finite number between {minimum} and {maximum}")
        return float(value)

    grid_voltage = optional_number("grid_voltage", grid_voltage, 0, 500)
    if grid_connected is not None and not isinstance(grid_connected, bool):
        raise TypeError("grid_connected must be bool or None")
    battery_current = optional_number("battery_current", battery_current, -1000, 1000)
    battery_capacity_ah = optional_number("battery_capacity_ah", battery_capacity_ah, 0.000001, 2000)
    shutdown_soc = optional_number("shutdown_soc", shutdown_soc, 0, 100)
    if battery_power_watts is not None and (
        not isinstance(battery_power_watts, int)
        or isinstance(battery_power_watts, bool)
        or not -32768 <= battery_power_watts <= 32767
    ):
        raise ValueError("battery_power_watts must be a signed 16-bit integer or None")
    battery_voltage = optional_number("battery_voltage", battery_voltage, 0.000001, 100)
    battery_temperature_c = optional_number("battery_temperature_c", battery_temperature_c, -99.9, 200)
    inverter_dc_temperature_c = optional_number(
        "inverter_dc_temperature_c", inverter_dc_temperature_c, -99.9, 200
    )
    inverter_radiator_temperature_c = optional_number(
        "inverter_radiator_temperature_c", inverter_radiator_temperature_c, -99.9, 200
    )
    runtime_eta_seconds = optional_number("runtime_eta_seconds", runtime_eta_seconds, 0, 3_153_600_000)
    charge_eta_seconds = optional_number("charge_eta_seconds", charge_eta_seconds, 0, 3_153_600_000)
    battery_cutoff_soc = optional_number("battery_cutoff_soc", battery_cutoff_soc, 0, 100)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO telemetry_samples (
              observed_at, is_grid_online, load_watts, battery_soc, total_load_energy_kwh,
              grid_voltage, grid_connected, battery_current, battery_capacity_ah, shutdown_soc,
              battery_power_watts, battery_voltage, battery_temperature_c,
              inverter_dc_temperature_c, inverter_radiator_temperature_c,
              runtime_eta_seconds, charge_eta_seconds, battery_cutoff_soc
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
            ON CONFLICT (observed_at) DO UPDATE SET
              is_grid_online = EXCLUDED.is_grid_online,
              load_watts = EXCLUDED.load_watts,
              battery_soc = EXCLUDED.battery_soc,
              total_load_energy_kwh = EXCLUDED.total_load_energy_kwh,
              grid_voltage = EXCLUDED.grid_voltage,
              grid_connected = EXCLUDED.grid_connected,
              battery_current = EXCLUDED.battery_current,
              battery_capacity_ah = EXCLUDED.battery_capacity_ah,
              shutdown_soc = EXCLUDED.shutdown_soc,
              battery_power_watts = EXCLUDED.battery_power_watts,
              battery_voltage = EXCLUDED.battery_voltage,
              battery_temperature_c = EXCLUDED.battery_temperature_c,
              inverter_dc_temperature_c = EXCLUDED.inverter_dc_temperature_c,
              inverter_radiator_temperature_c = EXCLUDED.inverter_radiator_temperature_c,
              runtime_eta_seconds = EXCLUDED.runtime_eta_seconds,
              charge_eta_seconds = EXCLUDED.charge_eta_seconds,
              battery_cutoff_soc = EXCLUDED.battery_cutoff_soc
            """,
            observed_at,
            is_grid_online,
            load_value,
            None if battery_soc is None else int(round(battery_soc)),
            None if total_load_energy_kwh is None else float(total_load_energy_kwh),
            grid_voltage,
            grid_connected,
            battery_current,
            battery_capacity_ah,
            shutdown_soc,
            battery_power_watts,
            battery_voltage,
            battery_temperature_c,
            inverter_dc_temperature_c,
            inverter_radiator_temperature_c,
            runtime_eta_seconds,
            charge_eta_seconds,
            battery_cutoff_soc,
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
