import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import EditMessageText, PinChatMessage
from support import IsolatedTestConfig

from deye_notifier.bot import handlers
from deye_notifier.database import chats
from deye_notifier.database import live_status as repository
from deye_notifier.inverter.charging import ChargingEstimate
from deye_notifier.inverter.client import InverterReading
from deye_notifier.services.live_status import LiveStatusService, NoTelemetryError


class LiveStatusTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.entries = {}
        self.bot = SimpleNamespace(
            send_message=AsyncMock(
                side_effect=[SimpleNamespace(message_id=100), SimpleNamespace(message_id=101)]
            ),
            edit_message_text=AsyncMock(),
            pin_chat_message=AsyncMock(),
            unpin_chat_message=AsyncMock(),
            delete_message=AsyncMock(),
        )
        now = datetime.now(timezone.utc)
        self.monitor = SimpleNamespace(
            latest_reading=InverterReading(230, True, 76, 0, -73.7, 200, 20, 402.5, -4046, 54.9),
            latest_reading_at=now,
            last_status_time=now - timedelta(hours=2),
            charging_estimate=lambda: ChargingEstimate(True, 2344),
            check=AsyncMock(side_effect=AssertionError("live status must not poll inverter")),
            read_inverter=Mock(side_effect=AssertionError("live status must not poll inverter")),
        )
        self.provider = SimpleNamespace(next_event_for_state=lambda _: ("", ""))
        self.service = LiveStatusService(None, self.bot, self.monitor, self.provider, self.settings)

        async def save(pool, chat_id, message_id):
            self.entries[chat_id] = dict(chat_id=chat_id, message_id=message_id, language="en")

        async def delete(pool, chat_id, message_id):
            if chat_id in self.entries and self.entries[chat_id]["message_id"] == message_id:
                del self.entries[chat_id]

        self.enterContext(patch.object(repository, "ensure_preferences", AsyncMock()))
        self.enterContext(patch.object(repository, "save_entry", AsyncMock(side_effect=save)))
        self.enterContext(patch.object(repository, "delete_entry", AsyncMock(side_effect=delete)))
        self.enterContext(
            patch.object(
                repository,
                "get_entry",
                AsyncMock(side_effect=lambda pool, chat_id: self.entries.get(chat_id)),
            )
        )
        self.enterContext(
            patch.object(
                repository, "get_entries", AsyncMock(side_effect=lambda pool: list(self.entries.values()))
            )
        )
        self.enterContext(patch.object(chats, "get_chat_language", AsyncMock(return_value="en")))

    def active_entry(self):
        self.entries[123] = dict(chat_id=123, message_id=99, language="en")

    async def test_repeated_and_concurrent_commands_leave_only_newest_message_active(self):
        await asyncio.gather(self.service.enable(123), self.service.enable(123))
        self.assertEqual(self.entries[123]["message_id"], 101)
        self.assertEqual(len(self.entries), 1)
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.bot.unpin_chat_message.assert_awaited_once_with(chat_id=123, message_id=100, request_timeout=10)
        self.assertIn("inactive", self.bot.edit_message_text.await_args.kwargs["text"])
        self.bot.edit_message_text.reset_mock()
        self.monitor.latest_reading = replace(self.monitor.latest_reading, battery_soc=77)
        await self.service.update_all()
        self.assertEqual(self.bot.edit_message_text.await_args.kwargs["message_id"], 101)
        self.monitor.check.assert_not_awaited()
        self.monitor.read_inverter.assert_not_called()

    async def test_restart_reuses_saved_message_and_stop_prevents_later_edits(self):
        self.active_entry()
        await self.service.update_all()
        self.assertEqual(self.bot.edit_message_text.await_args.kwargs["message_id"], 99)
        self.bot.send_message.assert_not_awaited()
        await self.service.stop(123)
        self.assertEqual(self.entries, {})
        self.bot.unpin_chat_message.assert_awaited_once_with(chat_id=123, message_id=99, request_timeout=10)
        self.bot.edit_message_text.reset_mock()
        await self.service.update_all()
        self.bot.edit_message_text.assert_not_awaited()

    async def test_unchanged_text_does_not_edit_again(self):
        self.active_entry()
        with patch.object(self.service, "_render", return_value="same status"):
            await self.service.update_all()
            await self.service.update_all()
        self.bot.edit_message_text.assert_awaited_once()

    async def test_missing_or_inaccessible_message_stops_without_sending_replacements(self):
        method = EditMessageText(text="x", chat_id=123, message_id=99)
        for error in (
            TelegramBadRequest(method=method, message="message to edit not found"),
            TelegramForbiddenError(method=method, message="bot was blocked by the user"),
        ):
            self.active_entry()
            self.bot.edit_message_text.side_effect = error
            await self.service.update_all()
            self.assertEqual(self.entries, {})
        self.bot.send_message.assert_not_awaited()

    async def test_retry_after_defers_updates_without_extra_inverter_reads(self):
        self.active_entry()
        self.bot.edit_message_text.side_effect = TelegramRetryAfter(
            method=EditMessageText(text="x", chat_id=123, message_id=99),
            message="flood control",
            retry_after=120,
        )
        with patch("deye_notifier.services.live_status.time.monotonic", return_value=100):
            await self.service.update_all()
            await self.service.update_all()
        self.bot.edit_message_text.assert_awaited_once()
        self.assertEqual(self.service._paused_until, 220)
        self.assertIn(123, self.entries)

    async def test_pin_permission_failure_keeps_message_updating(self):
        self.bot.pin_chat_message.side_effect = TelegramBadRequest(
            method=PinChatMessage(chat_id=123, message_id=100), message="not enough rights to pin a message"
        )
        self.assertFalse(await self.service.enable(123))
        self.monitor.latest_reading = replace(self.monitor.latest_reading, battery_soc=77)
        await self.service.update_all()
        self.assertEqual(self.bot.edit_message_text.await_args.kwargs["message_id"], 100)

    async def test_failed_save_keeps_old_entry_and_cleans_up_new_message(self):
        self.active_entry()
        with patch.object(repository, "save_entry", AsyncMock(side_effect=RuntimeError("DB failure"))):
            with self.assertRaisesRegex(RuntimeError, "DB failure"):
                await self.service.enable(123)
        self.assertEqual(self.entries[123]["message_id"], 99)
        self.bot.delete_message.assert_awaited_once_with(123, 100, request_timeout=10)
        self.bot.unpin_chat_message.assert_not_awaited()

    async def test_no_reading_does_not_create_a_message_or_poll_hardware(self):
        self.monitor.latest_reading = None
        with self.assertRaises(NoTelemetryError):
            await self.service.enable(123)
        self.bot.send_message.assert_not_awaited()
        self.monitor.check.assert_not_awaited()

    async def test_stop_command_also_stops_live_status(self):
        service = SimpleNamespace(stop=AsyncMock())
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123), from_user=SimpleNamespace(language_code="en"), answer=AsyncMock()
        )
        with patch.object(chats, "unsubscribe_chat", AsyncMock()) as unsubscribe:
            await handlers.cmd_stop(message, None, service)
        service.stop.assert_awaited_once_with(123)
        unsubscribe.assert_awaited_once_with(None, 123)

    async def test_transient_pinning_failure_reports_active_status_and_preserves_new_entry(self):
        for error in (
            TelegramNetworkError(
                method=PinChatMessage(chat_id=123, message_id=100), message="network failure"
            ),
            TelegramRetryAfter(
                method=PinChatMessage(chat_id=123, message_id=100), message="flood control", retry_after=120
            ),
        ):
            with self.subTest(error=type(error).__name__):
                self.bot.pin_chat_message.side_effect = error
                self.assertFalse(await self.service.enable(123))
                self.assertIn(123, self.entries)
                if isinstance(error, TelegramRetryAfter):
                    self.assertGreater(self.service._paused_until, 0)
