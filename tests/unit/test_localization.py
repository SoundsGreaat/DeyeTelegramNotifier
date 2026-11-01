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
            self.assertEqual(locales.get_runtime_line(language, 80, 1000, 0, 20, 0.9), "")
            self.assertEqual(locales.get_runtime_line(language, 80, 0, 10, 20, 0.9), "")

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
            self.assertNotIn("Unknown gaps:", report)


class MessageCopyTests(IsolatedTestConfig, unittest.TestCase):
    def test_charge_estimates_are_localized_and_do_not_claim_unknown_times(self):
        for language in locales.LANGUAGES:
            known = locales.get_charge_time_line(language, 1800)
            unknown = locales.get_charge_time_line(language, None)
            self.assertIn(locales.format_duration(1800, language), known)
            self.assertEqual(unknown, "")
            self.assertNotEqual(known, unknown)
            self.assertNotIn("(", known)

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

    def test_optional_unavailable_estimates_and_duration_are_silent_in_all_languages(self):
        for language in locales.LANGUAGES:
            for seconds in (None, -1, float("nan"), float("inf")):
                self.assertEqual(locales.get_charge_time_line(language, seconds), "")
            self.assertEqual(locales.get_state_duration_line(language, True, None), "")
            self.assertEqual(locales.get_runtime_line(language, None, 1000, 10, 20), "")

    def test_reports_hide_empty_decorations_but_keep_real_gaps_and_outages(self):
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        for language in locales.LANGUAGES:
            stats = make_stats()
            stats.update(
                online_seconds=7200,
                offline_seconds=0,
                unknown_seconds=0,
                coverage_percent=100,
                outages=0,
                longest_outage_seconds=0,
            )
            report = locales.format_statistics(language, "day", start, start + timedelta(hours=2), stats)
            self.assertNotIn("⚪", report)
            outage_line = next(line for line in report.splitlines() if line.startswith("🔌"))
            self.assertTrue(outage_line.endswith(": 0"))
            self.assertNotIn(" · ", outage_line)
            stats.update(
                online_seconds=3600,
                offline_seconds=1800,
                unknown_seconds=1800,
                coverage_percent=75,
                outages=1,
                longest_outage_seconds=1800,
            )
            report = locales.format_statistics(language, "day", start, start + timedelta(hours=2), stats)
            self.assertIn("⚪", report)
            self.assertIn(locales.format_duration(1800, language), report)

    def test_charging_power_is_localized_and_missing_values_are_silent(self):
        for language in locales.LANGUAGES:
            line = locales.get_charge_power_line(language, 4046)
            self.assertIn("4046", line)
            message = locales.get_message(
                language,
                "battery_charging",
                level=76,
                charge_power_line=line,
                charge_time_line=locales.get_charge_time_line(language, 2340),
            )
            self.assertIn("76%", message)
            self.assertIn("4046", message)
            self.assertIn(locales.format_duration(2340, language), message)
            for watts in (None, 0, -100, float("nan"), float("inf")):
                self.assertEqual(locales.get_charge_power_line(language, watts), "")

    def test_power_and_battery_notifications_group_context_before_estimates_in_all_languages(self):
        for language in locales.LANGUAGES:
            duration = locales.format_saved_duration("2h 15m", language)
            self.assertEqual(duration, locales.format_duration(8100, language))
            load = locales.get_load_line(language, 800)
            battery = locales.get_battery_level_line(language, 75)
            voltage = locales.get_voltage_line(language, 215.1)
            forecast = locales.get_forecast_line(language, "turn_on", "01.11.2026 20:00")
            runtime = locales.get_runtime_line(
                language, 75, 800, 10, 20, runtime_seconds=12367, include_energy=False
            )
            for key in ("warning_battery", "battery_discharging", "battery_low"):
                text = locales.get_message(
                    language,
                    key,
                    duration=duration,
                    level=75,
                    load_line=load,
                    battery_level_line=battery,
                    runtime_line=runtime,
                    forecast_line=forecast,
                )
                self.assertLess(text.index("800"), text.index(runtime.strip()))
                self.assertEqual(text.count("75%"), 1)
                self.assertTrue(text.endswith(forecast if key == "warning_battery" else runtime))
                self.assertNotIn("{", text)
                self.assertNotIn("\n\n", text)
            restored = locales.get_message(
                language, "power_restored", duration=duration, voltage_line=voltage, forecast_line=forecast
            )
            self.assertLess(restored.index(duration), restored.index("215.1"))
            self.assertNotRegex(restored, r"\d(?:W|V|Вт|В)(?:\n|$)")

    def test_rounded_estimates_and_optional_measurements_do_not_add_placeholder_lines(self):
        for language in locales.LANGUAGES:
            self.assertEqual(locales.format_saved_duration("old duration", language), "old duration")
            self.assertIn(
                locales.format_duration(12360, language),
                locales.get_runtime_line(language, 75, 800, 0, 20, runtime_seconds=12367),
            )
            self.assertNotIn(
                locales.format_duration(12367, language),
                locales.get_runtime_line(language, 75, 800, 0, 20, runtime_seconds=12367),
            )
            self.assertEqual(locales.get_battery_level_line(language, None), "")
            self.assertEqual(locales.get_load_line(language, None), "")
            self.assertEqual(locales.get_voltage_line(language, None), "")


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
