"""API startup/shutdown shares the existing bot lifecycle and database pool."""

import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from support import IsolatedTestConfig

from deye_notifier import app


class ApplicationLifecycleTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    def dependencies(self, *, polling=None, api_wait=None, api_start=None):
        async def wait_forever(*args, **kwargs):
            await asyncio.Event().wait()

        self.cleanup_order = []

        def closer(name):
            async def close():
                self.cleanup_order.append(name)

            return AsyncMock(side_effect=close)

        self.pool = SimpleNamespace(close=closer("pool"))
        self.bot = SimpleNamespace(session=SimpleNamespace(close=closer("bot")))
        self.monitor = SimpleNamespace(run=wait_forever, close=closer("monitor"), running=True)
        self.notifier = SimpleNamespace(delivery_loop=wait_forever, running=True)
        self.server = SimpleNamespace(
            start=AsyncMock(side_effect=api_start),
            wait=AsyncMock(side_effect=api_wait or wait_forever),
            close=closer("api"),
        )
        self.dispatcher = SimpleNamespace(start_polling=AsyncMock(side_effect=polling))
        for name, value in (
            ("Bot", Mock(return_value=self.bot)),
            ("get_db_pool", AsyncMock(return_value=self.pool)),
            ("prepare_database", AsyncMock()),
            ("register_bot_commands", AsyncMock()),
            ("YasnoScheduleProvider", Mock(return_value=SimpleNamespace(run=wait_forever))),
            ("DeyeStatusMonitor", Mock(return_value=self.monitor)),
            ("NotificationService", Mock(return_value=self.notifier)),
            ("LiveStatusService", Mock(return_value=SimpleNamespace(run=wait_forever))),
            ("statistics_loop", wait_forever),
            ("create_dispatcher", Mock(return_value=self.dispatcher)),
            ("create_api", Mock()),
            ("ApiServer", Mock(return_value=self.server)),
        ):
            self.enterContext(patch.object(app, name, value))

    def enabled_settings(self):
        return replace(self.settings, api_enabled=True, api_token="synthetic_" + "x" * 40)

    async def test_api_stops_before_its_shared_pool_is_closed(self):
        self.dependencies()
        await app.main(self.enabled_settings())
        self.server.start.assert_awaited_once()
        self.assertEqual(self.cleanup_order, ["api", "monitor", "pool", "bot"])

    async def test_disabled_api_does_not_bind_a_server(self):
        self.dependencies()
        await app.main(self.settings)
        app.create_api.assert_not_called()
        app.ApiServer.assert_not_called()
        self.assertEqual(self.cleanup_order, ["monitor", "pool", "bot"])

    async def test_api_bind_failure_aborts_startup_and_closes_resources(self):
        self.dependencies(api_start=RuntimeError("synthetic bind failure"))
        with self.assertRaisesRegex(RuntimeError, "bind failure"):
            await app.main(self.enabled_settings())
        self.dispatcher.start_polling.assert_not_awaited()
        self.assertEqual(self.cleanup_order, ["api", "monitor", "pool", "bot"])

    async def test_unexpected_api_exit_stops_polling_and_surfaces_failure(self):
        cancelled = asyncio.Event()

        async def polling(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def api_wait():
            return

        self.dependencies(polling=polling, api_wait=api_wait)
        with self.assertRaisesRegex(RuntimeError, "API server stopped unexpectedly"):
            await app.main(self.enabled_settings())
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.cleanup_order, ["api", "monitor", "pool", "bot"])


class ApplicationTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.bot = SimpleNamespace(session=SimpleNamespace(close=AsyncMock()))

    async def test_startup_registers_menu_after_migrations_before_polling(self):
        order = []

        async def migrate(settings):
            self.assertIs(settings, self.settings)
            order.append("migrate")

        async def register(bot):
            self.assertIs(bot, self.bot)
            order.append("register")

        async def poll(*args, **kwargs):
            self.assertIs(kwargs["settings"], self.settings)
            order.append("poll")

        async def close_monitor():
            order.append("monitor_closed")

        async def close_pool():
            order.append("pool_closed")

        monitor = SimpleNamespace(run=AsyncMock(), running=True, close=AsyncMock(side_effect=close_monitor))
        notifier = SimpleNamespace(delivery_loop=AsyncMock(), running=True)
        provider = SimpleNamespace(run=AsyncMock())
        pool = SimpleNamespace(close=AsyncMock(side_effect=close_pool))
        with (
            patch.object(app, "Bot", return_value=self.bot),
            patch.object(app, "get_db_pool", AsyncMock(return_value=pool)),
            patch.object(app, "prepare_database", migrate),
            patch.object(app, "register_bot_commands", register),
            patch.object(app, "DeyeStatusMonitor", return_value=monitor),
            patch.object(app, "NotificationService", return_value=notifier),
            patch.object(app, "YasnoScheduleProvider", return_value=provider),
            patch.object(app, "statistics_loop", AsyncMock()),
            patch.object(app, "create_dispatcher", return_value=SimpleNamespace(start_polling=poll)),
        ):
            await app.main(self.settings)
        self.assertEqual(order, ["migrate", "register", "poll", "monitor_closed", "pool_closed"])
        monitor.close.assert_awaited_once()
        pool.close.assert_awaited_once()
        self.bot.session.close.assert_awaited_once()
        self.assertFalse(monitor.running)
        self.assertFalse(notifier.running)

    async def test_failed_migration_closes_pool_without_starting_workers(self):
        pool = SimpleNamespace(close=AsyncMock())
        with (
            patch.object(app, "Bot", return_value=self.bot),
            patch.object(app, "get_db_pool", AsyncMock(return_value=pool)),
            patch.object(app, "prepare_database", AsyncMock(side_effect=RuntimeError("bad migration"))),
            patch.object(app, "DeyeStatusMonitor") as monitor,
            patch.object(app, "create_dispatcher") as dispatcher,
        ):
            with self.assertRaisesRegex(RuntimeError, "bad migration"):
                await app.main(self.settings)
        pool.close.assert_awaited_once()
        self.bot.session.close.assert_awaited_once()
        monitor.assert_not_called()
        dispatcher.assert_not_called()

    async def test_missing_token_fails_before_constructing_clients(self):
        with patch.object(app, "Bot") as bot:
            with self.assertRaisesRegex(ValueError, "TELEGRAM_TOKEN"):
                await app.main(replace(self.settings, telegram_token=None))
        bot.assert_not_called()
