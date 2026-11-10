"""Public API contracts. Values are measurements, never formatted bot messages."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class TelemetrySample(PublicModel):
    observed_at: datetime = Field(description="Measurement time, UTC ISO 8601.")
    is_grid_online: bool = Field(description="Incoming grid voltage is above 100 V; independent of relay.")
    load_watts: int
    battery_soc: int | None = Field(default=None, description="Battery state of charge, percent.")
    total_load_energy_kwh: float | None = None
    grid_voltage: float | None = Field(default=None, description="Incoming utility voltage, V.")
    grid_connected: bool | None = Field(default=None, description="Inverter grid relay; null if unknown.")
    battery_current: float | None = Field(
        default=None, description="DC current, A; positive discharge, negative charge."
    )
    battery_capacity_ah: float | None = None
    shutdown_soc: float | None = Field(default=None, description="Shutdown SOC read from inverter, percent.")
    battery_power_watts: int | None = Field(
        default=None, description="DC power; positive discharge, negative charge."
    )
    battery_voltage: float | None = Field(default=None, description="Battery voltage, V.")
    battery_temperature_c: float | None = Field(
        default=None, description="Common battery temperature, not individual packs."
    )
    inverter_dc_temperature_c: float | None = None
    inverter_radiator_temperature_c: float | None = None
    runtime_eta_seconds: float | None = Field(
        default=None, description="Estimated runtime to effective cutoff, seconds."
    )
    charge_eta_seconds: float | None = Field(default=None, description="Estimated time to 100%, seconds.")
    battery_cutoff_soc: float | None = Field(default=None, description="Effective shutdown SOC, percent.")


class StatusResponse(TelemetrySample):
    grid_present: bool
    grid_voltage_state: Literal["normal", "low", "high", "absent"]
    grid_state_since: datetime | None
    grid_state_duration_seconds: float | None
    age_seconds: float
    stale: bool
    charging: bool
    discharging: bool
    charge_power_watts: float | None
    discharge_power_watts: float | None
    cutoff_source: Literal["inverter", "configured"]


class HistoryResponse(PublicModel):
    start: datetime
    end: datetime
    items: list[TelemetrySample]
    next_cursor: datetime | None


class NumericSummary(PublicModel):
    avg: float | None
    min: float | None
    max: float | None


class AggregateBucket(PublicModel):
    bucket_start: datetime
    sample_count: int
    first_observed_at: datetime
    last_observed_at: datetime
    grid_present_fraction: float
    measurements: dict[str, NumericSummary]


class AggregateResponse(PublicModel):
    start: datetime
    end: datetime
    interval: Literal["minute", "hour", "day"]
    items: list[AggregateBucket]


class StatisticsValues(PublicModel):
    energy_kwh: float
    grid_energy_kwh: float | None = Field(
        default=None,
        description="Load energy during observed grid-present intervals, kWh; not utility import.",
    )
    battery_mode_energy_kwh: float | None = Field(
        default=None,
        description="Load energy during observed grid-absent intervals, kWh; not DC battery discharge.",
    )
    online_seconds: float
    offline_seconds: float
    unknown_seconds: float
    coverage_percent: float
    outages: int
    longest_outage_seconds: float
    avg_load_watts: float | None
    peak_load_watts: float | None
    min_soc: int | None
    max_soc: int | None


class StatisticsResponse(PublicModel):
    start: datetime
    end: datetime
    period: Literal["day", "week", "month", "custom"]
    timezone: str
    statistics: StatisticsValues


class Event(PublicModel):
    id: int
    message_key: str
    created_at: datetime
    voltage: float | None
    load_watts: int | None
    level: int | None
    threshold: int | None
    duration: str | None = Field(description="Legacy duration text captured with the event.")
    runtime_eta_seconds: float | None
    charge_eta_seconds: float | None
    charge_power_watts: float | None
    battery_cutoff_soc: float | None


class EventsResponse(PublicModel):
    items: list[Event]
    next_cursor: int | None


class ScheduleSlot(PublicModel):
    start: datetime
    end: datetime
    type: str


class ScheduleResponse(PublicModel):
    enabled: bool
    available: bool
    provider: str | None
    group: str | None
    timezone: str
    updated_at: datetime | None
    slots: list[ScheduleSlot]
    next_event: ScheduleSlot | None


class HealthResponse(PublicModel):
    status: Literal["ok", "degraded"]
    database_available: bool
    telemetry_available: bool
    observed_at: datetime | None
    age_seconds: float | None
    stale: bool


class ConfigurationResponse(PublicModel):
    check_interval_seconds: int
    stale_after_seconds: float
    timezone: str
    grid_presence_voltage: float
    grid_voltage_min: float
    grid_voltage_max: float
    grid_voltage_hysteresis: float
    battery_capacity_kwh: float
    battery_cutoff_soc: float
    battery_inverter_efficiency: float
