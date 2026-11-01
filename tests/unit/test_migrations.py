"""Offline checks for revision history, configuration and startup behavior."""

import io
import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from alembic import command
from alembic.script import ScriptDirectory
from support import IsolatedTestConfig

from deye_notifier import config as app_config
from deye_notifier.database import migrations as db_migrations


class MigrationConfigurationTests(IsolatedTestConfig, unittest.TestCase):
    def test_linear_revision_history_and_offline_upgrade(self):
        config = db_migrations.migration_config(self.settings)
        script = ScriptDirectory.from_config(config)
        self.assertEqual(script.get_heads(), ["0004_language_cleanup"])
        self.assertEqual(script.get_revision("0004_language_cleanup").down_revision, "0003_subscriptions")
        self.assertEqual(script.get_revision("0003_subscriptions").down_revision, "0002_reports")
        self.assertEqual(script.get_revision("0002_reports").down_revision, "0001_legacy")
        output = io.StringIO()
        config.output_buffer = output
        command.upgrade(config, "head", sql=True)
        sql = output.getvalue()
        self.assertEqual(sql.count("BEGIN;"), 1)
        self.assertEqual(sql.count("COMMIT;"), 1)
        self.assertIn("CREATE TABLE alembic_version", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS telemetry_samples", sql)
        self.assertIn("AT TIME ZONE 'UTC'", sql)

    def test_password_special_characters_are_not_interpolated(self):
        with (
            patch.dict(
                os.environ,
                {
                    "POSTGRES_USER": "user@local",
                    "POSTGRES_PASSWORD": "p@ss:%/word",
                    "POSTGRES_HOST": "127.0.0.1",
                    "POSTGRES_PORT": "5433",
                    "POSTGRES_DB": "test_db",
                    "LEGACY_DB_TIMEZONE": "UTC",
                },
            ),
            patch.object(app_config, "load_dotenv"),
        ):
            settings = app_config.Settings.from_database_env()
            config = db_migrations.migration_config(settings)
        url = config.attributes["database_url"]
        self.assertEqual(url.password, "p@ss:%/word")
        self.assertEqual(url.username, "user@local")
        self.assertEqual(url.port, 5433)
        self.assertNotIn("p@ss", str(url))
        self.assertIsNone(config.get_main_option("sqlalchemy.url"))

    def test_check_mode_refuses_old_or_unknown_schema(self):
        for actual in (
            (),
            ("0001_legacy",),
            ("0002_reports",),
            ("0003_subscriptions",),
            ("future_revision",),
        ):
            with patch.object(
                db_migrations.MigrationContext,
                "configure",
                return_value=SimpleNamespace(
                    get_current_heads=lambda: actual,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "alembic upgrade head"):
                    db_migrations._check_version(Mock(), db_migrations.migration_config(self.settings))

    def test_explicit_legacy_timezone_in_offline_sql(self):
        config = db_migrations.migration_config(self.settings)
        config.cmd_opts = SimpleNamespace(x=["legacy_timezone=Europe/Kyiv"])
        config.output_buffer = io.StringIO()
        command.upgrade(config, "head", sql=True)
        self.assertIn("AT TIME ZONE 'Europe/Kyiv'", config.output_buffer.getvalue())


class MigrationStartupTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    async def test_engine_is_disposed_on_migration_failure(self):
        connection = SimpleNamespace(run_sync=AsyncMock(side_effect=RuntimeError("bad migration")))

        class Engine:
            dispose = AsyncMock()

            @asynccontextmanager
            async def begin(self):
                yield connection

        engine = Engine()
        with (
            patch.object(db_migrations, "create_async_engine", return_value=engine),
            patch.dict(os.environ, {"DB_AUTO_MIGRATE": "true"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "bad migration"):
                await db_migrations.prepare_database(self.settings)
        engine.dispose.assert_awaited_once()

    async def test_disabled_auto_upgrade_checks_version(self):
        connection = SimpleNamespace(run_sync=AsyncMock())

        class Engine:
            dispose = AsyncMock()

            @asynccontextmanager
            async def begin(self):
                yield connection

        settings = self.settings.__class__(
            **{
                **self.settings.__dict__,
                "db_auto_migrate": False,
            }
        )
        with patch.object(db_migrations, "create_async_engine", return_value=Engine()):
            await db_migrations.prepare_database(settings)
        self.assertIs(connection.run_sync.await_args.args[0], db_migrations._check_version)
