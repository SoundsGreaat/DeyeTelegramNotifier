"""Integration tests against an explicitly supplied test PostgreSQL database.

Set TEST_POSTGRES_DSN=postgresql+asyncpg://... to enable. Every test uses a
uniquely named schema which is removed afterwards; application tables outside
that schema are never touched. No production settings are used by default.
"""

import os
import shutil
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import asyncpg
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from support import IsolatedTestConfig, local_test_database_url

from deye_notifier.database import chats as chat_repository
from deye_notifier.database import live_status as live_repository
from deye_notifier.database import migrations as db_migrations
from deye_notifier.database import monitor as state_repository
from deye_notifier.database import notifications as notification_repository
from deye_notifier.database import telemetry


@unittest.skipUnless(os.getenv("TEST_POSTGRES_DSN"), "TEST_POSTGRES_DSN is not configured")
class PostgresMigrationTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = "deye_migration_test_" + uuid.uuid4().hex
        url = local_test_database_url(os.environ["TEST_POSTGRES_DSN"])
        self.admin = create_async_engine(url, poolclass=NullPool, hide_parameters=True)
        async with self.admin.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {self.schema}"))
        self.engine = create_async_engine(
            url,
            poolclass=NullPool,
            hide_parameters=True,
            connect_args={"server_settings": {"search_path": self.schema}},
        )

    async def asyncTearDown(self):
        await self.engine.dispose()
        async with self.admin.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))
        await self.admin.dispose()

    async def sql(self, query):
        async with self.engine.begin() as connection:
            await connection.execute(text(f"SET LOCAL search_path TO {self.schema}"))
            result = await connection.execute(text(query))
            return result.all() if result.returns_rows else None

    async def migrate(self, target="head", *, downgrade=False, x=(), scripts=None):
        config = db_migrations.migration_config(self.settings)
        config.cmd_opts = SimpleNamespace(x=list(x))
        if scripts is not None:
            config.set_main_option("script_location", str(scripts))

        def run(connection):
            config.attributes["connection"] = connection
            (command.downgrade if downgrade else command.upgrade)(config, target)

        async with self.engine.begin() as connection:
            await connection.execute(text(f"SET LOCAL search_path TO {self.schema}"))
            await connection.run_sync(run)

    async def test_fresh_database_and_repeat_upgrade(self):
        await self.migrate()
        await self.migrate()
        self.assertEqual(await self.sql("SELECT version_num FROM alembic_version"), [("0008_live_status",)])
        tables = await self.sql("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
        self.assertEqual(
            {row[0] for row in tables},
            {
                "chats",
                "monitor_state",
                "notification_queue",
                "schedule_cache",
                "telemetry_samples",
                "alembic_version",
                "live_status_messages",
            },
        )

    async def test_live_message_migration_preferences_replacement_and_guarded_downgrade(self):
        await self.migrate("0007_charge_power")
        await self.sql(
            "INSERT INTO chats (chat_id, language, notifications_enabled, stats_enabled) VALUES (123, 'de', TRUE, TRUE)"
        )
        await self.migrate()
        url = local_test_database_url(os.environ["TEST_POSTGRES_DSN"])

        async def setup(connection):
            await connection.execute(f"SET search_path TO {self.schema}")

        pool = await asyncpg.create_pool(
            **url.translate_connect_args(username="user"), min_size=1, max_size=1, setup=setup
        )
        try:
            await live_repository.ensure_preferences(pool, 123, "uk")
            await live_repository.ensure_preferences(pool, 456, "uk")
            await live_repository.save_entry(pool, 123, 100)
            await live_repository.save_entry(pool, 123, 101)
            await live_repository.save_entry(pool, 456, 200)
            self.assertEqual((await live_repository.get_entry(pool, 123))["message_id"], 101)
            self.assertEqual(len(await live_repository.get_entries(pool)), 2)
            self.assertEqual(await chat_repository.get_chat_language(pool, 123), "de")
            self.assertEqual(await chat_repository.get_chats(pool), [(123, "de")])
            self.assertEqual(await chat_repository.get_chats(pool, stats_only=True), [(123, "de")])
            await live_repository.delete_entry(
                pool, 123, 100
            )  # An old updater cannot remove its replacement.
            self.assertIsNotNone(await live_repository.get_entry(pool, 123))
            await chat_repository.remove_chat(pool, 456)
            self.assertIsNone(await live_repository.get_entry(pool, 456))
        finally:
            await pool.close()
        with self.assertRaisesRegex(RuntimeError, "active live status preferences"):
            await self.migrate("0007_charge_power", downgrade=True)
        await self.migrate("0007_charge_power", downgrade=True, x=["allow_data_loss=true"])
        self.assertEqual(await self.sql("SELECT count(*) FROM chats"), [(1,)])
        await self.migrate()
        self.assertEqual(await self.sql("SELECT count(*) FROM live_status_messages"), [(0,)])

    async def test_old_unversioned_database_preserves_subscribers_and_time(self):
        await self.sql(
            "CREATE TABLE chats (chat_id BIGINT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        await self.sql("INSERT INTO chats (chat_id) VALUES (123)")
        await self.sql(
            "CREATE TABLE monitor_state (id BOOLEAN PRIMARY KEY, is_grid_online BOOLEAN NOT NULL, last_status_time TIMESTAMP NOT NULL)"
        )
        await self.sql("INSERT INTO monitor_state VALUES (TRUE, TRUE, '2026-01-01 12:00:00')")
        await self.migrate(x=["legacy_timezone=Europe/Kyiv"])
        self.assertEqual(await self.sql("SELECT chat_id, language FROM chats"), [(123, "en")])
        timestamp = (await self.sql("SELECT last_status_time FROM monitor_state"))[0][0]
        self.assertEqual(timestamp, datetime(2026, 1, 1, 10, tzinfo=timezone.utc))

    async def test_retired_language_is_migrated_without_changing_subscriptions(self):
        await self.migrate("0003_subscriptions")
        await self.sql("""
            INSERT INTO chats (chat_id, language, notifications_enabled, stats_enabled) VALUES
            (1, 'ru', TRUE, FALSE), (2, 'ru', FALSE, TRUE),
            (3, 'en', TRUE, TRUE), (4, 'uk', TRUE, TRUE),
            (5, 'es', FALSE, FALSE), (6, 'de', TRUE, FALSE)
        """)
        await self.migrate()
        expected = [
            (1, "en", True, False),
            (2, "en", False, True),
            (3, "en", True, True),
            (4, "uk", True, True),
            (5, "es", False, False),
            (6, "de", True, False),
        ]
        self.assertEqual(
            await self.sql(
                "SELECT chat_id, language, notifications_enabled, stats_enabled FROM chats ORDER BY chat_id"
            ),
            expected,
        )
        await self.migrate("0003_subscriptions", downgrade=True)
        await self.migrate()
        self.assertEqual(
            await self.sql(
                "SELECT chat_id, language, notifications_enabled, stats_enabled FROM chats ORDER BY chat_id"
            ),
            expected,
        )

    async def test_subscriptions_are_independent_and_legacy_reports_default_off(self):
        await self.migrate("0002_reports")
        await self.sql("INSERT INTO chats (chat_id, language) VALUES (123, 'uk')")
        await self.migrate()
        self.assertEqual(
            await self.sql("SELECT notifications_enabled, stats_enabled FROM chats"), [(True, False)]
        )
        url = local_test_database_url(os.environ["TEST_POSTGRES_DSN"])

        async def setup(connection):
            await connection.execute(f"SET search_path TO {self.schema}")

        pool = await asyncpg.create_pool(
            **url.translate_connect_args(username="user"),
            min_size=1,
            max_size=1,
            setup=setup,
        )
        try:
            self.assertEqual(await chat_repository.get_chats(pool), [(123, "uk")])
            self.assertEqual(await chat_repository.get_chats(pool, stats_only=True), [])
            self.assertEqual(await chat_repository.set_stats_subscription(pool, 123, True, "de"), "uk")
            await chat_repository.add_chat(pool, 123, "uk")  # /start preserves an explicit opt-in.
            self.assertEqual(await chat_repository.get_chats(pool, stats_only=True), [(123, "uk")])

            await chat_repository.set_stats_subscription(pool, 456, True, "de")
            self.assertEqual(await chat_repository.get_chats(pool), [(123, "uk")])
            self.assertEqual(
                sorted(await chat_repository.get_chats(pool, stats_only=True)), [(123, "uk"), (456, "de")]
            )
            await chat_repository.set_stats_subscription(pool, 123, False)
            self.assertEqual(await chat_repository.get_chats(pool), [(123, "uk")])
            self.assertEqual(await chat_repository.get_chats(pool, stats_only=True), [(456, "de")])

            await chat_repository.unsubscribe_chat(pool, 456)
            self.assertEqual(await chat_repository.get_chats(pool, stats_only=True), [])
            await chat_repository.add_chat(pool, 456, "de")
            self.assertEqual(await chat_repository.get_chats(pool, stats_only=True), [])
            self.assertEqual(sorted(await chat_repository.get_chats(pool)), [(123, "uk"), (456, "de")])
        finally:
            await pool.close()

    async def test_battery_forecasts_survive_queue_persistence_and_old_rows(self):
        await self.migrate("0004_language_cleanup")
        await self.sql(
            "INSERT INTO notification_queue (message_key, level) VALUES ('battery_discharging', 75)"
        )
        await self.migrate()
        self.assertEqual(await self.sql("SELECT charge_eta_seconds FROM notification_queue"), [(None,)])
        url = local_test_database_url(os.environ["TEST_POSTGRES_DSN"])

        async def setup(connection):
            await connection.execute(f"SET search_path TO {self.schema}")

        pool = await asyncpg.create_pool(
            **url.translate_connect_args(username="user"), min_size=1, max_size=1, setup=setup
        )
        try:
            await notification_repository.enqueue_notification(
                pool,
                "battery_charging",
                level=75,
                load=1000,
                charge_eta_seconds=1800,
                charge_power_watts=4046,
            )
            events = await notification_repository.get_pending_notifications(pool)
            self.assertEqual(events[-1]["charge_eta_seconds"], 1800)
            self.assertEqual(events[-1]["charge_power_watts"], 4046)
            self.assertIsNone(events[0]["charge_power_watts"])
            self.assertEqual(events[-1]["load_watts"], 1000)
            await state_repository.record_transition(
                pool,
                False,
                datetime.now(timezone.utc),
                0,
                1000,
                "1h",
                battery_soc=75,
                runtime_eta_seconds=43200,
                battery_cutoff_soc=15,
            )
            events = await notification_repository.get_pending_notifications(pool)
            self.assertEqual(events[-1]["message_key"], "warning_battery")
            self.assertEqual((events[-1]["level"], events[-1]["load_watts"]), (75, 1000))
            self.assertEqual(events[-1]["runtime_eta_seconds"], 43200)
            self.assertEqual(events[-1]["battery_cutoff_soc"], 15)
            now = datetime.now(timezone.utc)
            await telemetry.record_sample(pool, now, False, 1000, 75, total_load_energy_kwh=402.4)
            samples = await telemetry.fetch_samples(pool, now, now)
            self.assertEqual(samples[-1]["total_load_energy_kwh"], 402.4)
        finally:
            await pool.close()
        with self.assertRaisesRegex(RuntimeError, "measured energy history"):
            await self.migrate("0005_battery_eta", downgrade=True)
        await self.migrate("0004_language_cleanup", downgrade=True, x=["allow_data_loss=true"])
        self.assertEqual(await self.sql("SELECT count(*) FROM telemetry_samples"), [(1,)])
        self.assertEqual(await self.sql("SELECT count(*) FROM notification_queue"), [(3,)])
        await self.migrate()

    async def test_subscription_downgrade_requires_consent_and_does_not_enable_alerts(self):
        await self.migrate()
        await self.sql(
            "INSERT INTO chats (chat_id, notifications_enabled, stats_enabled) VALUES (123, TRUE, FALSE), (456, FALSE, TRUE)"
        )
        with self.assertRaisesRegex(RuntimeError, "subscription preferences"):
            await self.migrate("0002_reports", downgrade=True)
        self.assertEqual(await self.sql("SELECT version_num FROM alembic_version"), [("0008_live_status",)])
        await self.migrate("0002_reports", downgrade=True, x=["allow_data_loss=true"])
        self.assertEqual(await self.sql("SELECT chat_id FROM chats"), [(123,)])

    async def test_current_unversioned_schema_preserves_history_and_queue(self):
        # This is the last schema shipped before Alembic was introduced.
        await self.migrate("0002_reports")
        await self.sql("INSERT INTO chats (chat_id, language) VALUES (123, 'de')")
        await self.sql("INSERT INTO monitor_state VALUES (TRUE, TRUE, '2026-01-01 12:00:00+00')")
        await self.sql("INSERT INTO telemetry_samples VALUES ('2026-01-01 12:00:00+00', TRUE, 500, 80)")
        await self.sql(
            "INSERT INTO notification_queue (message_key, report_key, report_payload) VALUES ('statistics','test', '{}')"
        )
        await self.sql("DROP TABLE alembic_version")
        await self.migrate(x=["legacy_timezone=Europe/Kyiv"])
        self.assertEqual(await self.sql("SELECT chat_id, language FROM chats"), [(123, "de")])
        self.assertEqual(await self.sql("SELECT count(*) FROM telemetry_samples"), [(1,)])
        self.assertEqual(await self.sql("SELECT report_key FROM notification_queue"), [("test",)])
        timestamp = (await self.sql("SELECT last_status_time FROM monitor_state"))[0][0]
        self.assertEqual(timestamp, datetime(2026, 1, 1, 12, tzinfo=timezone.utc))

    async def test_downgrade_reupgrade_and_guarded_data_loss(self):
        await self.migrate()
        await self.migrate("0001_legacy", downgrade=True)
        await self.migrate()
        await self.sql("INSERT INTO telemetry_samples VALUES (CURRENT_TIMESTAMP, TRUE, 500, 80)")
        await self.sql(
            "INSERT INTO notification_queue (message_key, report_key, report_payload) VALUES ('statistics','test', '{}')"
        )
        with self.assertRaisesRegex(RuntimeError, "allow_data_loss"):
            await self.migrate("0001_legacy", downgrade=True)
        self.assertEqual(await self.sql("SELECT version_num FROM alembic_version"), [("0008_live_status",)])
        self.assertEqual(await self.sql("SELECT count(*) FROM telemetry_samples"), [(1,)])
        await self.migrate("0001_legacy", downgrade=True, x=["allow_data_loss=true"])
        self.assertEqual(
            await self.sql("SELECT count(*) FROM notification_queue WHERE message_key='statistics'"), [(0,)]
        )
        with self.assertRaisesRegex(RuntimeError, "allow_data_loss"):
            await self.migrate("base", downgrade=True)
        self.assertEqual(await self.sql("SELECT version_num FROM alembic_version"), [("0001_legacy",)])
        await self.migrate()

    async def test_failed_revision_rolls_back_ddl_and_version(self):
        await self.migrate("0001_legacy")
        with TemporaryDirectory() as directory:
            scripts = Path(directory) / "migrations"
            shutil.copytree(db_migrations.ROOT / "migrations", scripts)
            (scripts / "versions" / "0009_failure.py").write_text(
                "from alembic import op\n"
                "revision = '0009_failure'\n"
                "down_revision = '0008_live_status'\n"
                "def upgrade():\n"
                "    op.execute('CREATE TABLE migration_should_rollback (id INTEGER)')\n"
                "    raise RuntimeError('synthetic migration failure')\n"
                "def downgrade():\n"
                "    pass\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "synthetic migration failure"):
                await self.migrate(scripts=scripts)
        self.assertEqual(await self.sql("SELECT version_num FROM alembic_version"), [("0001_legacy",)])
        self.assertEqual(await self.sql("SELECT to_regclass('migration_should_rollback')"), [(None,)])
        # The preceding successful revisions were in the same transaction and
        # must also be absent after the later revision failed.
        self.assertEqual(await self.sql("SELECT to_regclass('telemetry_samples')"), [(None,)])
