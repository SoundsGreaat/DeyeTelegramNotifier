"""Offline checks for revision history, configuration and startup behavior."""

import io
import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from alembic import command
from alembic.script import ScriptDirectory

import db_migrations
from support import IsolatedTestConfig


class MigrationConfigurationTests(IsolatedTestConfig, unittest.TestCase):
    def test_linear_revision_history_and_offline_upgrade(self):
        config = db_migrations.migration_config()
        script = ScriptDirectory.from_config(config)
        self.assertEqual(script.get_heads(), ['0004_language_cleanup'])
        self.assertEqual(script.get_revision('0004_language_cleanup').down_revision, '0003_subscriptions')
        self.assertEqual(script.get_revision('0003_subscriptions').down_revision, '0002_reports')
        self.assertEqual(script.get_revision('0002_reports').down_revision, '0001_legacy')
        output = io.StringIO()
        config.output_buffer = output
        command.upgrade(config, 'head', sql=True)
        sql = output.getvalue()
        self.assertEqual(sql.count('BEGIN;'), 1)
        self.assertEqual(sql.count('COMMIT;'), 1)
        self.assertIn('CREATE TABLE alembic_version', sql)
        self.assertIn('CREATE TABLE IF NOT EXISTS telemetry_samples', sql)
        self.assertIn("AT TIME ZONE 'UTC'", sql)

    def test_password_special_characters_are_not_interpolated(self):
        with patch.dict(os.environ, {
            'POSTGRES_USER': 'user@local', 'POSTGRES_PASSWORD': 'p@ss:%/word',
            'POSTGRES_HOST': '127.0.0.1', 'POSTGRES_PORT': '5433',
            'POSTGRES_DB': 'test_db', 'LEGACY_DB_TIMEZONE': 'UTC',
        }), patch.object(db_migrations, 'load_dotenv'):
            config = db_migrations.migration_config()
        url = config.attributes['database_url']
        self.assertEqual(url.password, 'p@ss:%/word')
        self.assertEqual(url.username, 'user@local')
        self.assertEqual(url.port, 5433)
        self.assertNotIn('p@ss', str(url))
        self.assertIsNone(config.get_main_option('sqlalchemy.url'))

    def test_check_mode_refuses_old_or_unknown_schema(self):
        for actual in ((), ('0001_legacy',), ('0002_reports',), ('0003_subscriptions',), ('future_revision',)):
            with patch.object(db_migrations.MigrationContext, 'configure', return_value=SimpleNamespace(
                get_current_heads=lambda: actual,
            )):
                with self.assertRaisesRegex(RuntimeError, 'alembic upgrade head'):
                    db_migrations._check_version(Mock(), db_migrations.migration_config())

    def test_explicit_legacy_timezone_in_offline_sql(self):
        config = db_migrations.migration_config()
        config.cmd_opts = SimpleNamespace(x=['legacy_timezone=Europe/Kyiv'])
        config.output_buffer = io.StringIO()
        command.upgrade(config, 'head', sql=True)
        self.assertIn("AT TIME ZONE 'Europe/Kyiv'", config.output_buffer.getvalue())


class MigrationStartupTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    async def test_engine_is_disposed_on_migration_failure(self):
        connection = SimpleNamespace(run_sync=AsyncMock(side_effect=RuntimeError('bad migration')))

        class Engine:
            dispose = AsyncMock()

            @asynccontextmanager
            async def begin(self):
                yield connection

        engine = Engine()
        with patch.object(db_migrations, 'create_async_engine', return_value=engine), \
                patch.dict(os.environ, {'DB_AUTO_MIGRATE': 'true'}):
            with self.assertRaisesRegex(RuntimeError, 'bad migration'):
                await db_migrations.prepare_database()
        engine.dispose.assert_awaited_once()

    async def test_disabled_auto_upgrade_checks_version(self):
        connection = SimpleNamespace(run_sync=AsyncMock())

        class Engine:
            dispose = AsyncMock()

            @asynccontextmanager
            async def begin(self):
                yield connection

        with patch.object(db_migrations, 'create_async_engine', return_value=Engine()), \
                patch.dict(os.environ, {'DB_AUTO_MIGRATE': 'false'}):
            await db_migrations.prepare_database()
        self.assertIs(connection.run_sync.await_args.args[0], db_migrations._check_version)

    async def test_failed_migration_closes_pool_without_starting_workers(self):
        # Import through the bot test module so a synthetic token is used.
        from test_bot import main
        pool = SimpleNamespace(close=AsyncMock())
        with patch.object(main, 'get_db_pool', AsyncMock(return_value=pool)), \
                patch.object(main, 'prepare_database', AsyncMock(side_effect=RuntimeError('bad migration'))), \
                patch.object(main, 'DeyeStatusMonitor') as monitor, \
                patch.object(main.dp, 'start_polling', AsyncMock()) as polling:
            with self.assertRaisesRegex(RuntimeError, 'bad migration'):
                await main.main()
        pool.close.assert_awaited_once()
        monitor.assert_not_called()
        polling.assert_not_awaited()
