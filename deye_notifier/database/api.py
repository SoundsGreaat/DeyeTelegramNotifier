"""Bounded, parameterized SELECT queries for the public monitoring API."""

TELEMETRY_COLUMNS = (
    "observed_at",
    "is_grid_online",
    "load_watts",
    "battery_soc",
    "total_load_energy_kwh",
    "grid_voltage",
    "grid_connected",
    "battery_current",
    "battery_capacity_ah",
    "shutdown_soc",
    "battery_power_watts",
    "battery_voltage",
    "battery_temperature_c",
    "inverter_dc_temperature_c",
    "inverter_radiator_temperature_c",
    "runtime_eta_seconds",
    "charge_eta_seconds",
    "battery_cutoff_soc",
)
AGGREGATE_COLUMNS = (
    "grid_voltage",
    "load_watts",
    "battery_soc",
    "battery_current",
    "battery_voltage",
    "battery_power_watts",
    "battery_temperature_c",
    "inverter_dc_temperature_c",
    "inverter_radiator_temperature_c",
)
EVENT_KEYS = (
    "warning_battery",
    "power_restored",
    "outage_load",
    "battery_full",
    "battery_charging",
    "battery_discharging",
    "battery_low",
    "grid_voltage_high",
    "grid_voltage_low",
    "grid_voltage_normal",
    "grid_disconnected",
    "grid_reconnected",
)


async def _fetch(pool, query, *args):
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            return await conn.fetch(query, *args, timeout=5)


async def fetch_history(pool, start, end, cursor, limit):
    return await _fetch(
        pool,
        f"SELECT {', '.join(TELEMETRY_COLUMNS)} FROM telemetry_samples "
        "WHERE observed_at >= $1 AND observed_at < $2 "
        "AND ($3::timestamptz IS NULL OR observed_at > $3) ORDER BY observed_at LIMIT $4",
        start,
        end,
        cursor,
        limit + 1,
    )


async def fetch_aggregate(pool, start, end, seconds):
    aggregates = ", ".join(
        f"{operation}({column}) AS {column}_{operation}"
        for column in AGGREGATE_COLUMNS
        for operation in ("avg", "min", "max")
    )
    return await _fetch(
        pool,
        "SELECT to_timestamp(floor(extract(epoch FROM observed_at) / $3) * $3) AS bucket_start, "
        "count(*) AS sample_count, min(observed_at) AS first_observed_at, "
        "max(observed_at) AS last_observed_at, avg(is_grid_online::int) AS grid_present_fraction, "
        f"{aggregates} FROM telemetry_samples "
        "WHERE observed_at >= $1 AND observed_at < $2 GROUP BY bucket_start ORDER BY bucket_start LIMIT 2001",
        start,
        end,
        seconds,
    )


async def fetch_events(pool, start, end, cursor, limit):
    return await _fetch(
        pool,
        "SELECT id, message_key, created_at AT TIME ZONE 'UTC' AS created_at, voltage, load_watts, "
        "level, threshold, duration, runtime_eta_seconds, charge_eta_seconds, charge_power_watts, battery_cutoff_soc "
        "FROM notification_queue WHERE message_key = ANY($1::varchar[]) "
        "AND ($2::timestamptz IS NULL OR created_at AT TIME ZONE 'UTC' >= $2) "
        "AND ($3::timestamptz IS NULL OR created_at AT TIME ZONE 'UTC' < $3) "
        "AND id > $4 ORDER BY id LIMIT $5",
        list(EVENT_KEYS),
        start,
        end,
        cursor,
        limit + 1,
    )


async def database_available(pool):
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            return await conn.fetchval("SELECT 1", timeout=2) == 1
