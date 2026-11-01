"""Application settings, loaded explicitly by entry points."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _number(name, default, minimum, maximum):
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _database_values():
    auto_migrate = os.getenv("DB_AUTO_MIGRATE", "true").strip().lower()
    if auto_migrate not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError("DB_AUTO_MIGRATE must be true or false")
    legacy_timezone = os.getenv("LEGACY_DB_TIMEZONE", "UTC")
    ZoneInfo(legacy_timezone)
    return dict(
        postgres_host=os.getenv("POSTGRES_HOST", "postgres"),
        postgres_user=os.getenv("POSTGRES_USER", "postgres"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        postgres_db=os.getenv("POSTGRES_DB", "deye_bot"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        db_auto_migrate=auto_migrate in {"true", "1", "yes"},
        legacy_db_timezone=legacy_timezone,
    )


@dataclass(frozen=True)
class Settings:
    inverter_ip: str = "192.168.1.151"
    logger_serial: int = 0
    telegram_token: str | None = field(default=None, repr=False)
    check_interval: int = 10
    log_level: str = "INFO"
    battery_capacity_kwh: float = 0
    battery_cutoff_soc: float = 20
    battery_inverter_efficiency: float = 0.9
    stats_timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))
    stats_report_hour: int = 9
    stats_auto_reports: bool = True
    postgres_host: str = "postgres"
    postgres_user: str = "postgres"
    postgres_password: str = field(default="postgres", repr=False)
    postgres_db: str = "deye_bot"
    postgres_port: int = 5432
    db_auto_migrate: bool = True
    legacy_db_timezone: str = "UTC"
    schedule_provider: str = ""
    schedule_region_id: str = ""
    schedule_dso_id: str = ""
    schedule_city_id: str = ""
    schedule_street: str = field(default="", repr=False)
    schedule_house: str = field(default="", repr=False)
    schedule_timezone: str = "UTC"
    schedule_refresh_interval: int = 600
    schedule_request_timeout: float = 4

    @property
    def stats_max_gap_seconds(self):
        return max(60, self.check_interval * 3)

    @classmethod
    def from_database_env(cls):
        """Migration commands do not depend on bot or inverter configuration."""
        load_dotenv(PROJECT_ROOT / ".env")
        return cls(**_database_values())

    @classmethod
    def from_env(cls):
        load_dotenv(PROJECT_ROOT / ".env")
        report_hour = int(os.getenv("STATS_REPORT_HOUR", "9"))
        if not 0 <= report_hour <= 23:
            raise ValueError("STATS_REPORT_HOUR must be an integer between 0 and 23")
        return cls(
            inverter_ip=os.getenv("INVERTER_IP", "192.168.1.151"),
            logger_serial=int(os.getenv("LOGGER_SERIAL", "0")),
            telegram_token=os.getenv("TELEGRAM_TOKEN"),
            check_interval=int(os.getenv("CHECK_INTERVAL", "10")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            battery_capacity_kwh=_number("BATTERY_CAPACITY_KWH", 0, 0, 100000),
            battery_cutoff_soc=_number("BATTERY_CUTOFF_SOC", 20, 0, 100),
            battery_inverter_efficiency=_number("BATTERY_INVERTER_EFFICIENCY", 0.9, 0.01, 1),
            stats_timezone=ZoneInfo(os.getenv("STATS_TIMEZONE", "UTC")),
            stats_report_hour=report_hour,
            stats_auto_reports=os.getenv("STATS_AUTO_REPORTS", "true").strip().lower()
            in {"true", "1", "yes"},
            **_database_values(),
            schedule_provider=os.getenv("SCHEDULE_PROVIDER", "").strip().lower(),
            schedule_region_id=os.getenv("SCHEDULE_REGION_ID", "").strip(),
            schedule_dso_id=os.getenv("SCHEDULE_DSO_ID", "").strip(),
            schedule_city_id=os.getenv("SCHEDULE_CITY_ID", "").strip(),
            schedule_street=os.getenv("SCHEDULE_STREET", "").strip(),
            schedule_house=os.getenv("SCHEDULE_HOUSE", "").strip(),
            schedule_timezone=os.getenv("SCHEDULE_TIMEZONE", "UTC").strip(),
            schedule_refresh_interval=max(60, int(os.getenv("SCHEDULE_REFRESH_INTERVAL", "600"))),
            schedule_request_timeout=max(1.0, float(os.getenv("SCHEDULE_REQUEST_TIMEOUT", "4"))),
        )
