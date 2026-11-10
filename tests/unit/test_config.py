import os
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

from support import IsolatedTestConfig

from deye_notifier.config import PROJECT_ROOT, Settings
from deye_notifier.database.migrations import migration_config


class ConfigurationTests(IsolatedTestConfig, unittest.TestCase):
    def test_api_is_opt_in_and_requires_a_separate_valid_secret(self):
        self.assertFalse(Settings.from_env().api_enabled)
        for token in ("", "short", "x" * 31, "x" * 257, "x" * 32 + " ", "т" * 32):
            with (
                self.subTest(length=len(token)),
                patch.dict(os.environ, {"API_ENABLED": "true", "API_TOKEN": token}),
            ):
                with self.assertRaisesRegex(ValueError, "API_TOKEN") as error:
                    Settings.from_env()
                if token:
                    self.assertNotIn(token, str(error.exception))
        with patch.dict(os.environ, {"API_ENABLED": "true", "API_TOKEN": "synthetic_test_token_" + "x" * 32}):
            settings = Settings.from_env()
        self.assertTrue(settings.api_enabled)
        self.assertNotIn(settings.api_token, repr(settings))

    def test_api_bind_and_port_are_validated_without_affecting_migrations(self):
        for env in (
            {"API_ENABLED": "maybe"},
            {"API_PORT": "0"},
            {"API_PORT": "65536"},
            {"API_PORT": "8000.5"},
            {"API_HOST": ""},
            {"API_HOST": "127.0.0.1/api"},
        ):
            with self.subTest(env=env), patch.dict(os.environ, env):
                with self.assertRaises(ValueError):
                    Settings.from_env()
                self.assertFalse(Settings.from_database_env().api_enabled)

    def test_both_entry_points_delegate_to_the_application_runner(self):
        with patch("deye_notifier.app.run") as run:
            runpy.run_path(str(PROJECT_ROOT / "main.py"), run_name="__main__")
            runpy.run_module("deye_notifier", run_name="__main__")
        self.assertEqual(run.call_count, 2)

    def test_imports_do_not_load_settings_or_construct_clients(self):
        script = """
from unittest.mock import patch
with patch('dotenv.load_dotenv', side_effect=AssertionError('dotenv read on import')), \\
     patch('aiogram.Bot', side_effect=AssertionError('bot constructed on import')):
    import deye_notifier.app
    import deye_notifier.database.migrations
    import main
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            env={**os.environ, "TELEGRAM_TOKEN": "", "LOGGER_SERIAL": "invalid"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_migrations_ignore_unrelated_bot_settings(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_TOKEN": "",
                "LOGGER_SERIAL": "invalid",
                "STATS_TIMEZONE": "invalid",
            },
        ):
            config = migration_config()
        self.assertEqual(config.attributes["database_url"].database, "deye_test")

    def test_invalid_report_hour_has_a_clear_error(self):
        with patch.dict(os.environ, {"STATS_REPORT_HOUR": "24"}):
            with self.assertRaisesRegex(ValueError, "STATS_REPORT_HOUR"):
                Settings.from_env()

    def test_min_load_defaults_to_ten_and_uses_an_inclusive_configured_threshold(self):
        with patch.dict(os.environ):
            os.environ.pop("MIN_LOAD_WATTS", None)
            settings = Settings.from_env()
        self.assertEqual(settings.min_load_watts, 10)
        for watts in (None, 0, 1, 5, 9):
            self.assertFalse(settings.has_meaningful_load(watts))
        self.assertTrue(settings.has_meaningful_load(10))
        with patch.dict(os.environ, {"MIN_LOAD_WATTS": "25"}):
            settings = Settings.from_env()
        self.assertFalse(settings.has_meaningful_load(24))
        self.assertTrue(settings.has_meaningful_load(25))
        self.assertTrue(settings.has_meaningful_load(26))

    def test_invalid_minimum_load_is_rejected(self):
        for value in ("-1", "nan", "inf", "invalid"):
            with self.subTest(value=value), patch.dict(os.environ, {"MIN_LOAD_WATTS": value}):
                with self.assertRaises(ValueError):
                    Settings.from_env()

    def test_grid_voltage_warning_settings_validate_limits_and_recovery_margin(self):
        with patch.dict(
            os.environ,
            {"GRID_VOLTAGE_MIN": "190", "GRID_VOLTAGE_MAX": "255", "GRID_VOLTAGE_HYSTERESIS": "3"},
        ):
            settings = Settings.from_env()
        self.assertEqual((settings.grid_voltage_min, settings.grid_voltage_max), (190, 255))
        self.assertEqual(settings.grid_voltage_hysteresis, 3)
        for overrides in (
            {"GRID_VOLTAGE_MIN": "nan"},
            {"GRID_VOLTAGE_MAX": "inf"},
            {"GRID_VOLTAGE_HYSTERESIS": "-1"},
            {"GRID_VOLTAGE_MIN": "270"},
            {"GRID_VOLTAGE_MIN": "250", "GRID_VOLTAGE_MAX": "255"},
        ):
            with self.subTest(overrides=overrides), patch.dict(os.environ, overrides):
                with self.assertRaises(ValueError):
                    Settings.from_env()

    def test_live_refresh_interval_accepts_five_seconds_and_keeps_default(self):
        with patch.dict(os.environ):
            os.environ.pop("LIVE_STATUS_INTERVAL", None)
            self.assertEqual(Settings.from_env().live_status_interval, 60)
        for value in ("5", "10", "30", "60", "3600"):
            with self.subTest(value=value), patch.dict(os.environ, {"LIVE_STATUS_INTERVAL": value}):
                self.assertEqual(Settings.from_env().live_status_interval, float(value))

    def test_invalid_optional_live_interval_does_not_prevent_startup(self):
        for value in ("0", "4", "-1", "nan", "inf", "3601", "", "invalid-private-value"):
            with self.subTest(value=value), patch.dict(os.environ, {"LIVE_STATUS_INTERVAL": value}):
                with self.assertLogs("deye_notifier.config", level="WARNING") as output:
                    self.assertEqual(Settings.from_env().live_status_interval, 60)
                self.assertIn("LIVE_STATUS_INTERVAL", output.output[0])
                self.assertNotIn("invalid-private-value", output.output[0])
