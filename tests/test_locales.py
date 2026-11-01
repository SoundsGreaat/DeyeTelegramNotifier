"""User-visible copy and compatibility with retired language preferences."""

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import locales
from support import IsolatedTestConfig
from test_bot import main


class MessageCopyTests(IsolatedTestConfig, unittest.TestCase):
    def test_battery_messages_show_current_level_only_once(self):
        for language in locales.LANGUAGES:
            for key in ('battery_charging', 'battery_discharging', 'battery_low'):
                text = locales.get_message(language, key, level=75, threshold=75)
                self.assertEqual(text.count('75%'), 1)
                self.assertEqual(len(text.splitlines()), 2)
                self.assertNotIn('(', text)

    def test_runtime_has_only_estimate_and_available_energy(self):
        for language in locales.LANGUAGES:
            text = locales.get_runtime_line(language, 80, 1000, 10, 20, .9)
            self.assertEqual(len(text.strip().splitlines()), 2)
            self.assertIn('5.40', text)
            self.assertNotIn('(', text)

    def test_retired_language_uses_english_in_all_renderers(self):
        self.assertEqual(set(locales.LANGUAGES), {'en', 'uk', 'es', 'de'})
        self.assertEqual(set(locales.MESSAGES), set(locales.LANGUAGES))
        self.assertEqual(locales.normalize_language('ru'), 'en')
        self.assertEqual(locales.get_message('ru', 'start'), locales.get_message('en', 'start'))
        self.assertEqual(locales.format_duration(3660, 'ru'), '1h 1m')
        self.assertEqual(locales.get_runtime_line('ru', 80, 1000, 10, 20, .9),
                         locales.get_runtime_line('en', 80, 1000, 10, 20, .9))
        self.assertEqual(locales.get_forecast_line('ru', 'turn_on', '18:00'),
                         locales.get_forecast_line('en', 'turn_on', '18:00'))


class LanguageCompatibilityTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    async def test_removed_language_is_not_offered_to_russian_telegram_clients(self):
        message = SimpleNamespace(from_user=SimpleNamespace(language_code='ru'), answer=AsyncMock())
        await main.cmd_lang(message)
        self.assertEqual(message.answer.await_args.args[0], locales.get_message('en', 'select_lang'))
        markup = message.answer.await_args.kwargs['reply_markup']
        callbacks = {button.callback_data for row in markup.inline_keyboard for button in row}
        self.assertEqual(callbacks, {'lang_en', 'lang_uk', 'lang_es', 'lang_de'})

    async def test_legacy_russian_button_selects_english(self):
        callback = SimpleNamespace(data='lang_ru', answer=AsyncMock(), message=SimpleNamespace(
            chat=SimpleNamespace(id=123), answer=AsyncMock(),
        ))
        with patch.object(main, 'set_chat_language', AsyncMock()) as save:
            await main.callback_lang(callback, None)
        save.assert_awaited_once_with(None, 123, 'en')
        callback.message.answer.assert_awaited_once_with(locales.get_message('en', 'lang_set'))

    async def test_stale_database_language_falls_back_to_english(self):
        class Pool:
            @asynccontextmanager
            async def acquire(self):
                yield SimpleNamespace(fetchval=AsyncMock(return_value='ru'))

        self.assertEqual(await main.get_chat_language(Pool(), 123), 'en')
