import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiogram import Bot, types
from support import IsolatedTestConfig, make_stats

from deye_notifier.app import create_dispatcher
from deye_notifier.database import chats
from deye_notifier.services import reports


class RouterTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
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
