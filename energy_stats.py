"""Persistent inverter telemetry and period statistics.

The monitor records point-in-time readings.  Statistics are calculated by
holding each reading until the next reading, while treating long polling gaps
as unknown instead of silently inventing consumption.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from numbers import Real
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo


DEFAULT_MAX_GAP_SECONDS = 120.0


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _utc(value: datetime) -> datetime:
    return _require_aware(value, "datetime").astimezone(timezone.utc)


def _local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def report_period(
    period: str,
    now: datetime,
    timezone_: ZoneInfo,
    completed: bool = False,
) -> tuple[datetime, datetime]:
    """Return the current or previous local day/week/month.

    Returned values carry ``timezone_``.  Consumers should convert them to UTC
    before measuring elapsed seconds, which keeps DST transitions correct.
    """
    _require_aware(now, "now")
    if not isinstance(timezone_, ZoneInfo):
        raise TypeError("timezone_ must be a zoneinfo.ZoneInfo")
    period = period.strip().lower()
    if period not in {"day", "week", "month"}:
        raise ValueError("period must be day, week, or month")

    local_now = now.astimezone(timezone_)
    if period == "day":
        start_day = local_now.date()
        if completed:
            start_day -= timedelta(days=1)
        start = _local_midnight(start_day, timezone_)
        end = _local_midnight(local_now.date(), timezone_) if completed else local_now
    elif period == "week":
        monday = local_now.date() - timedelta(days=local_now.weekday())
        if completed:
            monday -= timedelta(days=7)
        start = _local_midnight(monday, timezone_)
        end = _local_midnight(monday + timedelta(days=7), timezone_) if completed else local_now
    else:
        first = local_now.date().replace(day=1)
        if completed:
            previous_last = first - timedelta(days=1)
            first = previous_last.replace(day=1)
        start = _local_midnight(first, timezone_)
        if completed:
            end = _local_midnight(local_now.date().replace(day=1), timezone_)
        else:
            if first.month == 12:
                next_first = date(first.year + 1, 1, 1)
            else:
                next_first = date(first.year, first.month + 1, 1)
            end = min(_local_midnight(next_first, timezone_), local_now)
    return start, end


def _row_value(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return row[index]


def _normalise_samples(rows: Iterable[Any]) -> list[tuple[datetime, bool, int, int | None]]:
    result: list[tuple[datetime, bool, int, int | None]] = []
    for row in rows:
        observed_at = _utc(_row_value(row, "observed_at", 0))
        online = bool(_row_value(row, "is_grid_online", 1))
        load = int(_row_value(row, "load_watts", 2) or 0)
        soc_raw = _row_value(row, "battery_soc", 3)
        soc = None if soc_raw is None else int(soc_raw)
        result.append((observed_at, online, max(0, load), soc))
    result.sort(key=lambda item: item[0])
    deduped: list[tuple[datetime, bool, int, int | None]] = []
    for item in result:
        if deduped and deduped[-1][0] == item[0]:
            deduped[-1] = item
        else:
            deduped.append(item)
    return deduped


def _build_intervals(
    rows: Sequence[tuple[datetime, bool, int, int | None]],
    start: datetime,
    end: datetime,
    max_gap_seconds: float,
) -> tuple[list[tuple[datetime, datetime, float, bool, int]], float]:
    """Build valid held-value intervals and return (intervals, unknown seconds)."""
    start_utc, end_utc = _utc(start), _utc(end)
    total_seconds = max(0.0, (end_utc - start_utc).total_seconds())
    intervals: list[tuple[datetime, datetime, float, bool, int]] = []
    valid_seconds = 0.0
    for previous, current in zip(rows, rows[1:]):
        previous_at, online, load, _ = previous
        current_at = current[0]
        clipped_start = max(previous_at, start_utc)
        clipped_end = min(current_at, end_utc)
        if clipped_end <= clipped_start:
            continue
        gap_seconds = (current_at - previous_at).total_seconds()
        if gap_seconds > max_gap_seconds:
            continue
        seconds = (clipped_end - clipped_start).total_seconds()
        intervals.append((clipped_start, clipped_end, seconds, online, load))
        valid_seconds += seconds
    return intervals, max(0.0, total_seconds - valid_seconds)


async def record_sample(
    pool: Any,
    observed_at: datetime,
    is_grid_online: bool,
    load_watts: int | float,
    battery_soc: int | None,
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
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO telemetry_samples (observed_at, is_grid_online, load_watts, battery_soc)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (observed_at) DO UPDATE SET
              is_grid_online = EXCLUDED.is_grid_online,
              load_watts = EXCLUDED.load_watts,
              battery_soc = EXCLUDED.battery_soc
            """,
            observed_at,
            is_grid_online,
            load_value,
            None if battery_soc is None else int(round(battery_soc)),
        )


async def _fetch_samples(pool: Any, start: datetime, end: datetime) -> list[Any]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            WITH predecessor AS (
                SELECT observed_at, is_grid_online, load_watts, battery_soc
                FROM telemetry_samples
                WHERE observed_at < $1
                ORDER BY observed_at DESC
                LIMIT 1
            ), period AS (
                SELECT observed_at, is_grid_online, load_watts, battery_soc
                FROM telemetry_samples
                WHERE observed_at >= $1 AND observed_at < $2
            ), successor AS (
                SELECT observed_at, is_grid_online, load_watts, battery_soc
                FROM telemetry_samples
                WHERE observed_at >= $2
                ORDER BY observed_at ASC
                LIMIT 1
            )
            SELECT observed_at, is_grid_online, load_watts, battery_soc FROM predecessor
            UNION ALL
            SELECT observed_at, is_grid_online, load_watts, battery_soc FROM period
            UNION ALL
            SELECT observed_at, is_grid_online, load_watts, battery_soc FROM successor
            ORDER BY observed_at
            """,
            _utc(start),
            _utc(end),
        )


async def get_statistics(
    pool: Any,
    start: datetime,
    end: datetime,
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
) -> dict[str, Any]:
    """Calculate clipped, gap-aware statistics for ``[start, end)``."""
    start, end = _require_aware(start, "start"), _require_aware(end, "end")
    if end <= start:
        raise ValueError("end must be after start")
    if max_gap_seconds <= 0:
        raise ValueError("max_gap_seconds must be positive")

    rows = await _fetch_samples(pool, start, end)
    # A month can contain hundreds of thousands of samples.  Keep this CPU
    # work off the event loop used by aiogram and the delivery worker.
    result = await asyncio.to_thread(_calculate_statistics, rows, start, end, float(max_gap_seconds))
    return result


def _calculate_statistics(
    rows: Iterable[Any],
    start: datetime,
    end: datetime,
    max_gap_seconds: float,
) -> dict[str, Any]:
    rows = _normalise_samples(rows)
    start_utc, end_utc = _utc(start), _utc(end)
    intervals, unknown_seconds = _build_intervals(rows, start, end, max_gap_seconds)
    total_seconds = (end_utc - start_utc).total_seconds()
    online_seconds = sum(i[2] for i in intervals if i[3])
    offline_seconds = sum(i[2] for i in intervals if not i[3])
    energy_kwh = sum(i[4] * i[2] for i in intervals) / 3_600_000
    grid_energy_kwh = sum(i[4] * i[2] for i in intervals if i[3]) / 3_600_000
    battery_mode_energy_kwh = sum(i[4] * i[2] for i in intervals if not i[3]) / 3_600_000
    weighted_load_seconds = sum(i[4] * i[2] for i in intervals)
    avg_load = weighted_load_seconds / (online_seconds + offline_seconds) if intervals else None
    period_rows = [row for row in rows if start_utc <= row[0] < end_utc]
    # Include a latest in-period sample even when there is no successor yet;
    # its value is still useful for peak/SOC reporting, though not for energy.
    peak_load = max(
        [i[4] for i in intervals] + [row[2] for row in period_rows],
        default=None,
    )

    outages = 0
    for previous, current in zip(rows, rows[1:]):
        if not previous[1] or current[1]:
            continue
        current_at = current[0]
        if current_at < start_utc or current_at >= end_utc:
            continue
        if (current_at - previous[0]).total_seconds() <= max_gap_seconds:
            outages += 1

    longest_outage = 0.0
    running_outage = 0.0
    previous_end: datetime | None = None
    for interval in intervals:
        interval_start, interval_end, seconds, online, _ = interval
        if not online:
            if previous_end == interval_start:
                running_outage += seconds
            else:
                running_outage = seconds
            longest_outage = max(longest_outage, running_outage)
        else:
            running_outage = 0.0
        previous_end = interval_end

    soc_values = [row[3] for row in period_rows if row[3] is not None]
    coverage = ((online_seconds + offline_seconds) / total_seconds * 100.0) if total_seconds else 0.0
    return {
        "energy_kwh": energy_kwh,
        "grid_energy_kwh": grid_energy_kwh,
        "battery_mode_energy_kwh": battery_mode_energy_kwh,
        "online_seconds": online_seconds,
        "offline_seconds": offline_seconds,
        "unknown_seconds": unknown_seconds,
        "coverage_percent": max(0.0, min(100.0, coverage)),
        "outages": outages,
        "longest_outage_seconds": longest_outage,
        "avg_load_watts": avg_load,
        "peak_load_watts": peak_load,
        "min_soc": min(soc_values) if soc_values else None,
        "max_soc": max(soc_values) if soc_values else None,
    }

