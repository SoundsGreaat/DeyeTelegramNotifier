"""Synthetic configuration and network isolation shared by the test suite."""

import ipaddress
import os
import socket
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import make_url

from deye_notifier import config

TEST_ENV = {
    "TELEGRAM_TOKEN": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    "INVERTER_IP": "192.0.2.1",  # Reserved documentation address, never contacted.
    "LOGGER_SERIAL": "0",
    "CHECK_INTERVAL": "10",
    "LOG_LEVEL": "WARNING",
    "MIN_LOAD_WATTS": "10",
    "LIVE_STATUS_INTERVAL": "60",
    "GRID_VOLTAGE_MIN": "180",
    "GRID_VOLTAGE_MAX": "260",
    "GRID_VOLTAGE_HYSTERESIS": "5",
    "API_ENABLED": "false",
    "API_HOST": "127.0.0.1",
    "API_PORT": "8000",
    "API_TOKEN": "",
    "BATTERY_CAPACITY_KWH": "10",
    "BATTERY_CUTOFF_SOC": "20",
    "BATTERY_INVERTER_EFFICIENCY": "0.9",
    "STATS_TIMEZONE": "UTC",
    "STATS_REPORT_HOUR": "9",
    "STATS_AUTO_REPORTS": "true",
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "1",  # App DB connections must be mocked, even in CI.
    "POSTGRES_USER": "deye_test",
    "POSTGRES_PASSWORD": "deye_test",
    "POSTGRES_DB": "deye_test",
    "DB_AUTO_MIGRATE": "true",
    "LEGACY_DB_TIMEZONE": "UTC",
    "SCHEDULE_PROVIDER": "",
    "SCHEDULE_REGION_ID": "",
    "SCHEDULE_DSO_ID": "",
    "SCHEDULE_CITY_ID": "",
    "SCHEDULE_STREET": "",
    "SCHEDULE_HOUSE": "",
    "SCHEDULE_TIMEZONE": "UTC",
    "SCHEDULE_REFRESH_INTERVAL": "600",
    "SCHEDULE_REQUEST_TIMEOUT": "4",
}


@contextmanager
def isolated_config():
    # Keep OS/runtime variables and an explicitly supplied TEST_POSTGRES_DSN,
    # while overriding every application setting and disabling .env reads.
    with (
        patch.dict(os.environ, TEST_ENV),
        patch("dotenv.load_dotenv", return_value=False),
        patch.object(config, "load_dotenv", return_value=False),
    ):
        yield


def is_loopback(host):
    if isinstance(host, bytes):
        host = host.decode("ascii")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def local_test_database_url(dsn):
    url = make_url(dsn).set(drivername="postgresql+asyncpg")
    if not is_loopback(url.host) or url.database != "deye_test" or url.query:
        raise ValueError(
            "TEST_POSTGRES_DSN must point to a loopback database named deye_test without URL query parameters"
        )
    return url


class IsolatedTestConfig:
    """Mixin: real configuration and external network access are never used."""

    def setUp(self):
        super().setUp()
        self.enterContext(isolated_config())
        original_getaddrinfo = socket.getaddrinfo
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex

        def check_host(host):
            if not is_loopback(host):
                raise AssertionError("Tests cannot connect outside loopback; mock external services.")

        def getaddrinfo(host, *args, **kwargs):
            check_host(host)
            return original_getaddrinfo(host, *args, **kwargs)

        def connect(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                check_host(address[0])
            return original_connect(sock, address)

        def connect_ex(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                check_host(address[0])
            return original_connect_ex(sock, address)

        self.enterContext(patch("socket.getaddrinfo", getaddrinfo))
        self.enterContext(patch("socket.socket.connect", connect))
        self.enterContext(patch("socket.socket.connect_ex", connect_ex))
        self.settings = config.Settings.from_env()


def make_stats():
    return {
        "energy_kwh": 3.5,
        "online_seconds": 3600,
        "offline_seconds": 1800,
        "unknown_seconds": 600,
        "coverage_percent": 90,
        "outages": 1,
        "longest_outage_seconds": 1800,
        "avg_load_watts": 500,
        "peak_load_watts": 1500,
        "min_soc": 25,
        "max_soc": 100,
    }
