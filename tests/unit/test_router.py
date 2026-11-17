import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot, types
from aiogram.methods import EditMessageText
from support import IsolatedTestConfig, make_stats

from deye_notifier.app import create_dispatcher
from deye_notifier.database import chats
from deye_notifier.notification_preferences import PREFERENCE_KEYS
from deye_notifier.services import reports


class RouterTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    async def test_notifications_command_and_button_edit_the_same_menu(self):
        bot = Bot(token=self.settings.telegram_token)
        original_session = bot.session
        bot.session = AsyncMock(return_value=True)
        user = types.User(id=1, is_bot=False, first_name="Test", language_code="uk")
        chat = types.Chat(id=123, type="private")
        now = datetime.now(timezone.utc)
        command = types.Message(message_id=1, date=now, chat=chat, from_user=user, text="/notifications")
        menu = types.Message(message_id=2, date=now, chat=chat, text="Settings")
        before = dict.fromkeys(PREFERENCE_KEYS, False)
        after = {**before, "report_week": True}
        dispatcher = create_dispatcher()
        try:
            with (
                patch.object(chats, "get_notification_preferences", AsyncMock(return_value=("uk", before))),
                patch.object(
                    chats, "set_notification_preference", AsyncMock(return_value=("uk", after))
                ) as save,
            ):
                await dispatcher.feed_update(
                    bot, types.Update(update_id=1, message=command), pool=None, settings=self.settings
                )
                reply = bot.session.await_args.args[1]
                buttons = [row[0] for row in reply.reply_markup.inline_keyboard]
                self.assertEqual(len(buttons), len(PREFERENCE_KEYS))
                self.assertTrue(all(button.text.startswith("❌") for button in buttons))
                weekly = next(button for button in buttons if button.callback_data == "notify:report_week:1")
                callback = types.CallbackQuery(
                    id="1", from_user=user, chat_instance="test", message=menu, data=weekly.callback_data
                )
                await dispatcher.feed_update(
                    bot, types.Update(update_id=2, callback_query=callback), pool=None, settings=self.settings
                )
                save.assert_awaited_once_with(None, 123, "report_week", True, "uk")
                edit = bot.session.await_args.args[1]
                self.assertEqual(edit.message_id, menu.message_id)
                enabled = [
                    row[0] for row in edit.reply_markup.inline_keyboard if row[0].text.startswith("✅")
                ]
                self.assertEqual([button.callback_data for button in enabled], ["notify:report_week:0"])
        finally:
            await original_session.close()

    async def test_group_member_cannot_change_shared_notification_preferences(self):
        bot = Bot(token=self.settings.telegram_token)
        original_session = bot.session
        user = types.User(id=1, is_bot=False, first_name="Test")
        bot.session = AsyncMock(side_effect=[types.ChatMemberMember(user=user), True])
        message = types.Message(
            message_id=2, date=datetime.now(timezone.utc), chat=types.Chat(id=-123, type="group")
        )
        callback = types.CallbackQuery(
            id="1", from_user=user, chat_instance="test", message=message, data="notify:report_day:1"
        )
        try:
            with (
                patch.object(chats, "get_chat_language", AsyncMock(return_value="en")),
                patch.object(chats, "set_notification_preference", AsyncMock()) as save,
            ):
                await create_dispatcher().feed_update(
                    bot, types.Update(update_id=1, callback_query=callback), pool=None, settings=self.settings
                )
                save.assert_not_awaited()
                self.assertTrue(bot.session.await_args.args[1].show_alert)
        finally:
            await original_session.close()

    async def test_concurrent_presses_cannot_overwrite_a_newer_menu(self):
        bot = Bot(token=self.settings.telegram_token)
        original_session = bot.session
        user = types.User(id=1, is_bot=False, first_name="Test")
        message = types.Message(
            message_id=2, date=datetime.now(timezone.utc), chat=types.Chat(id=123, type="private")
        )
        preferences = dict.fromkeys(PREFERENCE_KEYS, False)
        first_edit_started = asyncio.Event()
        release_first_edit = asyncio.Event()
        completed_edits = []

        async def save(_pool, _chat, key, enabled, _language):
            preferences[key] = enabled
            return "en", preferences.copy()

        async def send(_bot, method, **_kwargs):
            if isinstance(method, EditMessageText):
                if not first_edit_started.is_set():
                    first_edit_started.set()
                    await release_first_edit.wait()
                completed_edits.append(method.reply_markup)
            return True

        bot.session = AsyncMock(side_effect=send)
        dispatcher = create_dispatcher()

        async def press(update_id, key):
            callback = types.CallbackQuery(
                id=str(update_id),
                from_user=user,
                chat_instance="test",
                message=message,
                data=f"notify:{key}:1",
            )
            await dispatcher.feed_update(
                bot,
                types.Update(update_id=update_id, callback_query=callback),
                pool=None,
                settings=self.settings,
            )

        try:
            with patch.object(chats, "set_notification_preference", side_effect=save):
                first = asyncio.create_task(press(1, "report_day"))
                await asyncio.wait_for(first_edit_started.wait(), timeout=2)
                second = asyncio.create_task(press(2, "report_week"))
                await asyncio.sleep(0)
                release_first_edit.set()
                await asyncio.gather(first, second)
            enabled = {
                row[0].callback_data
                for row in completed_edits[-1].inline_keyboard
                if row[0].text.startswith("✅")
            }
            self.assertEqual(enabled, {"notify:report_day:0", "notify:report_week:0"})
        finally:
            await original_session.close()

    async def test_report_shortcuts_receive_settings_through_dispatcher(self):
        for command, title in (("stats_week", "Weekly"), ("stats_month", "Monthly")):
            with self.subTest(command=command):
                bot = Bot(token=self.settings.telegram_token)
                original_session = bot.session
                user = types.User(id=1, is_bot=False, first_name="Test")
                chat = types.Chat(id=123, type="private")
                now = datetime.now(timezone.utc)
                wait = types.Message(message_id=2, date=now, chat=chat, text="wait").as_(bot)
                session = AsyncMock(side_effect=[wait, True])
                bot.session = session
                update = types.Update(
                    update_id=1,
                    message=types.Message(
                        message_id=1,
                        date=now,
                        chat=chat,
                        from_user=user,
                        text="/" + command,
                    ),
                )
                try:
                    with (
                        patch.object(chats, "get_chat_language", AsyncMock(return_value="en")),
                        patch.object(
                            reports, "query_statistics", AsyncMock(return_value=make_stats())
                        ) as query,
                    ):
                        await create_dispatcher().feed_update(bot, update, pool=None, settings=self.settings)
                    self.assertIs(query.await_args.args[-1], self.settings)
                    self.assertIn(title, session.await_args_list[-1].args[1].text)
                finally:
                    await original_session.close()

    async def test_live_commands_use_the_shared_service_from_dispatcher(self):
        for command, method in (("live", "enable"), ("live_off", "stop")):
            with self.subTest(command=command):
                bot = Bot(token=self.settings.telegram_token)
                original_session = bot.session
                bot.session = AsyncMock(return_value=True)
                service = SimpleNamespace(enable=AsyncMock(return_value=True), stop=AsyncMock())
                update = types.Update(
                    update_id=1,
                    message=types.Message(
                        message_id=1,
                        date=datetime.now(timezone.utc),
                        chat=types.Chat(id=123, type="private"),
                        from_user=types.User(id=1, is_bot=False, first_name="Test", language_code="uk"),
                        text="/" + command,
                    ),
                )
                try:
                    with patch.object(chats, "get_chat_language", AsyncMock(return_value="uk")):
                        await create_dispatcher().feed_update(
                            bot, update, pool=None, settings=self.settings, live_status_service=service
                        )
                    expected = (123, "uk") if method == "enable" else (123,)
                    getattr(service, method).assert_awaited_once_with(*expected)
                finally:
                    await original_session.close()
