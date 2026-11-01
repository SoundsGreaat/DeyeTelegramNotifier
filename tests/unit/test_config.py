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
