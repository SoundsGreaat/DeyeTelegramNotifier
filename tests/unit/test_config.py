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
