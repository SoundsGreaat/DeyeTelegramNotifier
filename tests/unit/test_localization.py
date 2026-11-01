import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from support import IsolatedTestConfig, make_stats

from deye_notifier.bot import handlers, locales
from deye_notifier.database import chats as chat_repository


class FormattingTests(IsolatedTestConfig, unittest.TestCase):
    def test_runtime_accounts_for_reserve_and_efficiency(self):
        for language in locales.LANGUAGES:
            line = locales.get_runtime_line(language, 80, 1000, 10, 20, 0.9)
            self.assertIn(locales.format_duration(5.4 * 3600, language), line)
            self.assertIn("5.40", line)
            self.assertIn("0", locales.get_runtime_line(language, 15, 1000, 10, 20, 0.9))
            self.assertIn("BATTERY_CAPACITY_KWH", locales.get_runtime_line(language, 80, 1000, 0, 20, 0.9))
            self.assertNotIn("inf", locales.get_runtime_line(language, 80, 0, 10, 20, 0.9))

    def test_reports_render_in_every_language(self):
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        for language in locales.LANGUAGES:
            for period in ("day", "week", "month"):
                report = locales.format_statistics(
                    language, period, start, start + timedelta(hours=2), make_stats()
                )
                self.assertIn("3.50", report)
                self.assertIn("25–100%", report)
                self.assertNotIn("{", report)
                self.assertLess(len(report), 4096)
            self.assertEqual(
                locales.format_statistics(language, "day", start, start, {}),
                locales.get_message(language, "stats_empty"),
            )

    def test_dst_report_does_not_invent_unknown_hours(self):
        tz = ZoneInfo("Europe/Berlin")
        for year, month, day, expected_hours in ((2025, 3, 30, 23), (2025, 10, 26, 25)):
            start = datetime(year, month, day, tzinfo=tz)
            end = start + timedelta(days=1)
            stats = make_stats()
            stats.update(
                online_seconds=expected_hours * 3600,
                offline_seconds=0,
                unknown_seconds=0,
                coverage_percent=100,
            )
            report = locales.format_statistics("en", "day", start, end, stats)
            self.assertIn(f"Grid available: {expected_hours:.1f} h (100%)", report)
            self.assertIn("Unknown gaps: 0.0 h (0%)", report)


class MessageCopyTests(IsolatedTestConfig, unittest.TestCase):
    def test_battery_messages_show_current_level_only_once(self):
        for language in locales.LANGUAGES:
            for key in ("battery_charging", "battery_discharging", "battery_low"):
                text = locales.get_message(language, key, level=75, threshold=75)
                self.assertEqual(text.count("75%"), 1)
                self.assertEqual(len(text.splitlines()), 2)
                self.assertNotIn("(", text)

    def test_runtime_has_only_estimate_and_available_energy(self):
        for language in locales.LANGUAGES:
            text = locales.get_runtime_line(language, 80, 1000, 10, 20, 0.9)
            self.assertEqual(len(text.strip().splitlines()), 2)
            self.assertIn("5.40", text)
            self.assertNotIn("(", text)

    def test_retired_language_uses_english_in_all_renderers(self):
        self.assertEqual(set(locales.LANGUAGES), {"en", "uk", "es", "de"})
        self.assertEqual(set(locales.MESSAGES), set(locales.LANGUAGES))
        self.assertEqual(locales.normalize_language("ru"), "en")
        self.assertEqual(locales.get_message("ru", "start"), locales.get_message("en", "start"))
        self.assertEqual(locales.format_duration(3660, "ru"), "1h 1m")
        self.assertEqual(
            locales.get_runtime_line("ru", 80, 1000, 10, 20, 0.9),
            locales.get_runtime_line("en", 80, 1000, 10, 20, 0.9),
        )
        self.assertEqual(
            locales.get_forecast_line("ru", "turn_on", "18:00"),
            locales.get_forecast_line("en", "turn_on", "18:00"),
        )


class LanguageCompatibilityTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    async def test_removed_language_is_not_offered_to_russian_telegram_clients(self):
        message = SimpleNamespace(from_user=SimpleNamespace(language_code="ru"), answer=AsyncMock())
        await handlers.cmd_lang(message)
        self.assertEqual(message.answer.await_args.args[0], locales.get_message("en", "select_lang"))
        markup = message.answer.await_args.kwargs["reply_markup"]
        callbacks = {button.callback_data for row in markup.inline_keyboard for button in row}
        self.assertEqual(callbacks, {"lang_en", "lang_uk", "lang_es", "lang_de"})

    async def test_legacy_russian_button_selects_english(self):
        callback = SimpleNamespace(
            data="lang_ru",
            answer=AsyncMock(),
            message=SimpleNamespace(
                chat=SimpleNamespace(id=123),
                answer=AsyncMock(),
            ),
        )
        with patch.object(chat_repository, "set_chat_language", AsyncMock()) as save:
            await handlers.callback_lang(callback, None)
        save.assert_awaited_once_with(None, 123, "en")
        callback.message.answer.assert_awaited_once_with(locales.get_message("en", "lang_set"))

    async def test_stale_database_language_falls_back_to_english(self):
        class Pool:
            @asynccontextmanager
            async def acquire(self):
                yield SimpleNamespace(fetchval=AsyncMock(return_value="ru"))

        self.assertEqual(await chat_repository.get_chat_language(Pool(), 123), "en")
