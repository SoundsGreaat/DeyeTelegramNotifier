import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from support import IsolatedTestConfig

from deye_notifier import app


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
