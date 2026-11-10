"""Read-only routes backed by the monitor's shared snapshot and PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime

from deye_notifier import statistics
from deye_notifier.database import api as repository
from deye_notifier.services.reports import query_statistics

from .models import (
    AggregateResponse,
    ConfigurationResponse,
    EventsResponse,
    HealthResponse,
    HistoryResponse,
    ScheduleResponse,
    StatisticsResponse,
    StatusResponse,
)
from .security import ReadOnlyAuthentication

MAX_PERIOD = timedelta(days=31)
INTERVALS = {"minute": 60, "hour": 3600, "day": 86400}
READING_FIELDS = (
    "grid_voltage",
    "grid_connected",
    "battery_soc",
    "load_watts",
    "battery_current",
    "battery_capacity_ah",
    "shutdown_soc",
    "total_load_energy_kwh",
    "battery_power_watts",
    "battery_voltage",
    "battery_temperature_c",
    "inverter_dc_temperature_c",
    "inverter_radiator_temperature_c",
)


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _error(code, message, status):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _period(start, end):
    start, end = _utc(start), _utc(end)
    if end <= start or end - start > MAX_PERIOD:
        raise HTTPException(422, "The range must be positive and at most 31 days.")
    return start, end


async def _read(operation):
    try:
        async with asyncio.timeout(10):
            return await operation
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Monitoring data is temporarily unavailable.") from None


def snapshot(monitor, settings, now):
    reading, observed = monitor.latest_reading, monitor.latest_reading_at
    if reading is None or observed is None:
        raise HTTPException(503, "No inverter measurement is available yet.")
    observed = _utc(observed)
    age = max(0, (now - observed).total_seconds())
    stale = age > settings.stats_max_gap_seconds
    present = reading.is_grid_online
    if present is None:
        raise HTTPException(503, "No valid inverter measurement is available.")
    voltage_state = (
        "absent"
        if not present
        else "low"
        if reading.grid_voltage < settings.grid_voltage_min
        else "high"
        if reading.grid_voltage > settings.grid_voltage_max
        else "normal"
    )
    since = getattr(monitor, "last_status_time", None)
    since = _utc(since) if since is not None else None
    # The snapshot can become visible just before a transition is persisted.
    if getattr(monitor, "is_grid_online", None) != present:
        since = None
    duration_end = observed if stale else now
    charge = monitor.charging_estimate() if not stale else None
    charging = reading.is_charging or bool(charge and charge.is_charging)
    charge_eta = charge.seconds_remaining if charge and charging and reading.battery_soc < 100 else None
    runtime = reading.runtime_seconds(settings) if not stale and not charging else None
    power = reading.battery_power_watts
    return {
        **{name: getattr(reading, name, None) for name in READING_FIELDS},
        "observed_at": observed,
        "is_grid_online": present,
        "grid_present": present,
        "grid_voltage_state": voltage_state,
        "grid_state_since": since,
        "grid_state_duration_seconds": max(0, (duration_end - since).total_seconds()) if since else None,
        "age_seconds": age,
        "stale": stale,
        "charging": charging,
        "discharging": reading.is_discharging,
        "charge_power_watts": reading.charge_power_watts,
        "discharge_power_watts": power if power is not None and power > 0 else None,
        "runtime_eta_seconds": runtime,
        "charge_eta_seconds": charge_eta,
        "battery_cutoff_soc": reading.cutoff_soc(settings),
        "cutoff_source": "inverter" if reading.shutdown_soc is not None else "configured",
    }


def _schedule(provider, monitor, settings, now):
    timezone = provider.timezone
    cache = provider.schedule if provider.enabled else None
    slots, valid_days = [], []
    if isinstance(cache, dict):
        for key in ("today", "tomorrow"):
            day = cache.get(key)
            if not isinstance(day, dict) or not isinstance(day.get("date"), str):
                continue
            try:
                date = datetime.fromisoformat(day["date"])
                date = date.replace(tzinfo=timezone) if date.tzinfo is None else date.astimezone(timezone)
                midnight = date.replace(hour=0, minute=0, second=0, microsecond=0)
            except ValueError:
                continue
            valid_days.append(midnight.date())
            for item in day.get("slots", []):
                try:
                    start, end = int(item["start"]), int(item["end"])
                    kind = item["type"]
                    if not (0 <= start < end <= 1440) or not isinstance(kind, str):
                        continue
                    slots.append(
                        {
                            "start": (midnight + timedelta(minutes=start)).astimezone(UTC),
                            "end": (midnight + timedelta(minutes=end)).astimezone(UTC),
                            "type": kind,
                        }
                    )
                except (TypeError, KeyError, ValueError):
                    continue
    slots.sort(key=lambda item: item["start"])
    available = bool(cache is not None and now.astimezone(timezone).date() in valid_days)
    next_slot = None
    reading = monitor.latest_reading
    if available and reading is not None and reading.is_grid_online is not None:
        for slot in slots:
            if slot["type"] not in provider.OUTAGE_TYPES:
                continue
            if reading.is_grid_online:
                matches = slot["start"] >= now
            else:
                matches = slot["start"] <= now < slot["end"]
            if matches:
                next_slot = slot
                break
    updated = None
    if isinstance(cache, dict) and isinstance(cache.get("updated_on"), str):
        try:
            updated = datetime.fromisoformat(cache["updated_on"])
            updated = updated.replace(tzinfo=timezone) if updated.tzinfo is None else updated
            updated = updated.astimezone(UTC)
        except ValueError:
            pass
    return {
        "enabled": bool(provider.enabled),
        "available": available,
        "provider": settings.schedule_provider if provider.enabled else None,
        "group": str(cache["group"]) if isinstance(cache, dict) and cache.get("group") is not None else None,
        "timezone": str(timezone),
        "updated_at": updated,
        "slots": slots,
        "next_event": next_slot,
    }


def create_api(pool, monitor, schedule_provider, settings):
    """Create an API sharing existing dependencies; never starts polling or writes data."""
    if not settings.api_token:
        raise ValueError("API_TOKEN is required to create the API")
    app = FastAPI(
        title="Deye Monitoring API", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_middleware(ReadOnlyAuthentication, token=settings.api_token)
    # Declare the bearer scheme for Swagger while authenticating before all validation.
    original_openapi = app.openapi

    def secured_openapi():
        schema = original_openapi()
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["ApiToken"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "API_TOKEN from server configuration.",
        }
        for path, operations in schema.get("paths", {}).items():
            if path.startswith("/api/"):
                for operation in operations.values():
                    if isinstance(operation, dict):
                        operation["security"] = [{"ApiToken": []}]
        return schema

    app.openapi = secured_openapi

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error_value):
        return _error("invalid_request", "Invalid query parameters; see the API documentation.", 422)

    @app.exception_handler(HTTPException)
    async def http_error(_request, error):
        return _error(
            "unavailable" if error.status_code == 503 else "invalid_request",
            str(error.detail),
            error.status_code,
        )

    @app.exception_handler(Exception)
    async def internal_error(_request, _error_value):
        return _error("unavailable", "Monitoring data is temporarily unavailable.", 503)

    @app.get("/docs", include_in_schema=False)
    async def docs():
        return get_swagger_ui_html(openapi_url="/openapi.json", title="Deye Monitoring API")

    @app.get("/openapi.json", include_in_schema=False)
    async def schema():
        return app.openapi()

    @app.get("/api/v1/status", response_model=StatusResponse, tags=["Monitoring"])
    async def status():
        """Latest shared measurement. Stale data is marked and has no battery forecasts."""
        return snapshot(monitor, settings, datetime.now(UTC))

    @app.get("/api/v1/config", response_model=ConfigurationResponse, tags=["Monitoring"])
    async def configuration():
        """Public monitoring thresholds, units and calendar configuration; no credentials."""
        return {
            "check_interval_seconds": settings.check_interval,
            "stale_after_seconds": settings.stats_max_gap_seconds,
            "timezone": str(settings.stats_timezone),
            "grid_presence_voltage": 100,
            **{
                name: getattr(settings, name)
                for name in (
                    "grid_voltage_min",
                    "grid_voltage_max",
                    "grid_voltage_hysteresis",
                    "battery_capacity_kwh",
                    "battery_cutoff_soc",
                    "battery_inverter_efficiency",
                )
            },
        }

    @app.get("/api/v1/history", response_model=HistoryResponse, tags=["History"])
    async def history(
        start: AwareDatetime,
        end: AwareDatetime,
        cursor: AwareDatetime | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ):
        """Ascending measurements in [start,end), with an exclusive timestamp cursor; max 31 days."""
        start, end = _period(start, end)
        if cursor is not None and not start <= cursor < end:
            raise HTTPException(422, "The cursor must be within the requested range.")
        rows = await _read(repository.fetch_history(pool, start, end, cursor, limit))
        return {
            "start": start,
            "end": end,
            "items": [dict(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1]["observed_at"] if len(rows) > limit else None,
        }

    @app.get("/api/v1/history/aggregate", response_model=AggregateResponse, tags=["History"])
    async def aggregate(
        start: AwareDatetime, end: AwareDatetime, interval: Literal["minute", "hour", "day"] = "hour"
    ):
        """UTC-aligned averages/min/max of observed points, without filling missing buckets."""
        start, end = _period(start, end)
        seconds = INTERVALS[interval]
        possible_buckets = int((end.timestamp() - 0.000001) // seconds - start.timestamp() // seconds + 1)
        if possible_buckets > 2000:
            raise HTTPException(422, "Choose a larger interval; at most 2000 buckets are allowed.")
        rows = await _read(repository.fetch_aggregate(pool, start, end, seconds))
        items = []
        for row in rows:
            items.append(
                {
                    **{
                        name: row[name]
                        for name in (
                            "bucket_start",
                            "sample_count",
                            "first_observed_at",
                            "last_observed_at",
                            "grid_present_fraction",
                        )
                    },
                    "measurements": {
                        name: {operation: row[f"{name}_{operation}"] for operation in ("avg", "min", "max")}
                        for name in repository.AGGREGATE_COLUMNS
                    },
                }
            )
        return {"start": start, "end": end, "interval": interval, "items": items}

    @app.get("/api/v1/statistics", response_model=StatisticsResponse, tags=["History"])
    async def reports(
        period: Literal["day", "week", "month"] = "day",
        completed: bool = False,
        start: AwareDatetime | None = None,
        end: AwareDatetime | None = None,
    ):
        """Consumption and availability for a calendar period or explicit range of at most 31 days."""
        if start is not None or end is not None:
            if start is None or end is None or completed:
                raise HTTPException(422, "Supply both start and end without completed=true.")
            start, end = _period(start, end)
            selected_period = "custom"
        else:
            start, end = statistics.report_period(
                period, datetime.now(UTC), settings.stats_timezone, completed
            )
            start, end = _utc(start), _utc(end)
            selected_period = period
        values = await _read(query_statistics(pool, start, end, settings))
        return {
            "start": start,
            "end": end,
            "period": selected_period,
            "timezone": str(settings.stats_timezone),
            "statistics": values,
        }

    @app.get("/api/v1/events", response_model=EventsResponse, tags=["History"])
    async def events(
        start: AwareDatetime | None = None,
        end: AwareDatetime | None = None,
        cursor: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ):
        """Public monitoring events only, ascending by id; excludes reports and Telegram preferences."""
        if start is not None and end is not None and end <= start:
            raise HTTPException(422, "End must follow start.")
        rows = await _read(repository.fetch_events(pool, start, end, cursor, limit))
        return {
            "items": [dict(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
        }

    @app.get("/api/v1/schedule", response_model=ScheduleResponse, tags=["Monitoring"])
    async def schedule():
        """Cached city schedule, without address data or requests to the schedule provider."""
        return _schedule(schedule_provider, monitor, settings, datetime.now(UTC))

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["Monitoring"])
    async def health():
        """Database reachability and telemetry freshness. Returns 503 when either is unavailable."""
        database_ok = False
        try:
            async with asyncio.timeout(3):
                database_ok = await repository.database_available(pool)
        except Exception:
            pass
        observed = monitor.latest_reading_at
        observed = _utc(observed) if observed is not None else None
        age = max(0, (datetime.now(UTC) - observed).total_seconds()) if observed else None
        available = monitor.latest_reading is not None and observed is not None
        stale = age is None or age > settings.stats_max_gap_seconds
        healthy = database_ok and available and not stale
        result = HealthResponse(
            status="ok" if healthy else "degraded",
            database_available=database_ok,
            telemetry_available=available,
            observed_at=observed,
            age_seconds=age,
            stale=stale,
        )
        return JSONResponse(result.model_dump(mode="json"), status_code=200 if healthy else 503)

    return app
