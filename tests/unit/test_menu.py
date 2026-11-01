import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from aiogram import types
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SetMyCommands
from support import IsolatedTestConfig

from deye_notifier.bot import locales, menu


class CommandMenuTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.bot = SimpleNamespace(set_my_commands=AsyncMock(), delete_my_commands=AsyncMock())

    async def test_menu_is_english_and_has_week_and_month_shortcuts(self):
        with (
            patch.object(self.bot, "set_my_commands", AsyncMock()) as register,
            patch.object(self.bot, "delete_my_commands", AsyncMock()) as delete,
        ):
            await menu.register_bot_commands(self.bot)
        self.assertEqual(
            {item.kwargs["language_code"] for item in register.await_args_list}, {"", *locales.LANGUAGES}
        )
        for item in register.await_args_list:
            self.assertEqual(item.kwargs["scope"].type, "default")
            commands = {command.command: command.description for command in item.args[0]}
            self.assertEqual(commands["stats_week"], "This week's electricity statistics")
            self.assertEqual(commands["stats_month"], "This month's electricity statistics")
            self.assertTrue(all(description.isascii() for description in commands.values()))
        delete.assert_awaited_once_with(
            scope=types.BotCommandScopeDefault(), language_code="ru", request_timeout=10
        )

    async def test_transient_api_failure_is_retried(self):
        error = TelegramNetworkError(method=SetMyCommands(commands=[]), message="synthetic failure")
        with (
            patch.object(
                self.bot,
                "set_my_commands",
                AsyncMock(side_effect=[error] + [True] * (len(locales.LANGUAGES) + 1)),
            ) as register,
            patch.object(self.bot, "delete_my_commands", AsyncMock()),
            patch.object(asyncio, "sleep", AsyncMock()) as sleep,
        ):
            await menu.register_bot_commands(self.bot)
        self.assertEqual(register.await_count, 1 + len(locales.LANGUAGES) + 1)
        sleep.assert_awaited_once_with(1)

    async def test_api_outage_does_not_abort_startup(self):
        error = TelegramNetworkError(method=SetMyCommands(commands=[]), message="synthetic failure")
        with (
            patch.object(self.bot, "set_my_commands", AsyncMock(side_effect=error)) as register,
            patch.object(asyncio, "sleep", AsyncMock()) as sleep,
        ):
            await menu.register_bot_commands(self.bot)
        self.assertEqual(register.await_count, 3)
        self.assertEqual(sleep.await_args_list, [call(1), call(2)])
