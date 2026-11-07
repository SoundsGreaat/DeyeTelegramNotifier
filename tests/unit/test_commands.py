import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.filters import CommandObject
from support import IsolatedTestConfig

from deye_notifier.bot import handlers, locales
from deye_notifier.database import chats as chat_repository
from deye_notifier.inverter.charging import ChargingEstimate
from deye_notifier.inverter.client import InverterReading


class CommandTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    async def test_status_uses_shared_monitor_and_displays_duration_and_runtime(self):
        wait = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(chat=SimpleNamespace(id=123), answer=AsyncMock(return_value=wait))
        monitor = SimpleNamespace(
            check=AsyncMock(return_value=InverterReading(230, True, 80, 1000, battery_current=20)),
            last_status_time=datetime.now(timezone.utc) - timedelta(hours=2),
            charging_estimate=lambda: ChargingEstimate(False, None),
        )
        provider = SimpleNamespace(next_event_for_state=lambda _: ("", ""))
        with patch.object(chat_repository, "get_chat_language", AsyncMock(return_value="uk")):
            await handlers.cmd_status(message, None, provider, monitor, self.settings)
        text = wait.edit_text.await_args.args[0]
        self.assertIn("Світло є вже: 2г", text)
        self.assertIn("Напруга: 230.0 В", text)
        self.assertIn("Навантаження: 1000 Вт", text)
        self.assertLess(text.index("Світло є вже:"), text.index("Напруга:"))
        self.assertLess(text.index("Напруга:"), text.index("Навантаження:"))
        self.assertLess(text.index("Навантаження:"), text.index("Батарея:"))
        self.assertIn("5г 24хв", text)
        monitor.check.assert_awaited_once()

    async def test_invalid_stats_argument_gets_usage(self):
        message = SimpleNamespace(chat=SimpleNamespace(id=123), answer=AsyncMock())
        with patch.object(chat_repository, "get_chat_language", AsyncMock(return_value="uk")):
            await handlers.cmd_stats(
                message, None, CommandObject(command="stats", args="year"), self.settings
            )
        message.answer.assert_awaited_once_with(locales.get_message("uk", "stats_usage"))

    async def test_status_shows_time_to_full_charge_while_charging(self):
        wait = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(chat=SimpleNamespace(id=123), answer=AsyncMock(return_value=wait))
        monitor = SimpleNamespace(
            check=AsyncMock(return_value=InverterReading(230, True, 80, 1000)),
            last_status_time=datetime.now(timezone.utc),
            charging_estimate=lambda: ChargingEstimate(True, 1800),
        )
        with patch.object(chat_repository, "get_chat_language", AsyncMock(return_value="uk")):
            await handlers.cmd_status(
                message,
                None,
                SimpleNamespace(next_event_for_state=lambda _: ("", "")),
                monitor,
                self.settings,
            )
        text = wait.edit_text.await_args.args[0]
        self.assertIn("До 100%: приблизно 30хв", text)
        self.assertNotIn("5г 24хв", text)

    def message(self):
        return SimpleNamespace(
            chat=SimpleNamespace(id=123), from_user=SimpleNamespace(language_code="uk"), answer=AsyncMock()
        )

    async def test_stats_commands_save_explicit_choice_and_respect_saved_language(self):
        for command, enabled, key in (
            ("stats_auto_on", True, "stats_auto_enabled"),
            ("stats_auto_off", False, "stats_auto_disabled"),
        ):
            message = self.message()
            with patch.object(
                chat_repository, "set_stats_subscription", AsyncMock(return_value="de")
            ) as save:
                await handlers.cmd_stats_auto(message, None, CommandObject(command=command), self.settings)
            save.assert_awaited_once_with(None, 123, enabled, "uk")
            message.answer.assert_awaited_once_with(locales.get_message("de", key))

    async def test_global_pause_is_explained_when_saving_subscription(self):
        message = self.message()
        with patch.object(chat_repository, "set_stats_subscription", AsyncMock(return_value="uk")):
            await handlers.cmd_stats_auto(
                message,
                None,
                CommandObject(command="stats_auto_on"),
                replace(self.settings, stats_auto_reports=False),
            )
        self.assertIn(locales.get_message("uk", "stats_auto_paused"), message.answer.await_args.args[0])

    async def test_start_only_enables_power_notifications(self):
        message = self.message()
        with (
            patch.object(chat_repository, "add_chat", AsyncMock()) as add,
            patch.object(chat_repository, "set_stats_subscription", AsyncMock()) as stats,
        ):
            await handlers.cmd_start(message, None)
        add.assert_awaited_once_with(None, 123, "uk")
        stats.assert_not_awaited()

    async def test_stop_turns_off_all_subscriptions(self):
        message = self.message()
        with patch.object(chat_repository, "unsubscribe_chat", AsyncMock()) as unsubscribe:
            await handlers.cmd_stop(message, None)
        unsubscribe.assert_awaited_once_with(None, 123)

    async def test_status_uses_measured_runtime_and_shutdown_without_configured_kwh(self):
        wait = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(chat=SimpleNamespace(id=123), answer=AsyncMock(return_value=wait))
        monitor = SimpleNamespace(
            check=AsyncMock(return_value=InverterReading(0, False, 75, 500, 10, 200, 15)),
            last_status_time=datetime.now(timezone.utc),
            charging_estimate=lambda: ChargingEstimate(False, None),
        )
        with patch.object(chat_repository, "get_chat_language", AsyncMock(return_value="en")):
            await handlers.cmd_status(
                message,
                None,
                SimpleNamespace(next_event_for_state=lambda _: ("", "")),
                monitor,
                replace(self.settings, battery_capacity_kwh=0),
            )
        result = wait.edit_text.await_args.args[0]
        self.assertIn("Estimated runtime to 15%: 12h", result)
        self.assertIn("Usable reserve: 6.00 kWh", result)
        self.assertIn("Offline", result)
        self.assertNotIn("Voltage:", result)
        self.assertNotIn("set BATTERY_CAPACITY_KWH", result)

    async def test_charging_status_omits_runtime_for_zero_or_positive_load_in_every_language(self):
        for language in locales.LANGUAGES:
            for load, charge_seconds in ((0, 1800), (1000, 1800), (0, None)):
                with self.subTest(language=language, load=load, charge_seconds=charge_seconds):
                    wait = SimpleNamespace(edit_text=AsyncMock())
                    message = SimpleNamespace(
                        chat=SimpleNamespace(id=123), answer=AsyncMock(return_value=wait)
                    )
                    monitor = SimpleNamespace(
                        check=AsyncMock(
                            return_value=InverterReading(
                                230, True, 80, load, -80, 200, 20, battery_power_watts=-4046
                            )
                        ),
                        last_status_time=datetime.now(timezone.utc),
                        charging_estimate=lambda: ChargingEstimate(True, charge_seconds),
                    )
                    with (
                        patch.object(chat_repository, "get_chat_language", AsyncMock(return_value=language)),
                        patch.object(locales, "get_runtime_line") as runtime,
                    ):
                        await handlers.cmd_status(
                            message,
                            None,
                            SimpleNamespace(next_event_for_state=lambda _: ("", "")),
                            monitor,
                            self.settings,
                        )
                    runtime.assert_not_called()
                    text = wait.edit_text.await_args.args[0]
                    self.assertIn("80%", text)
                    self.assertIn("4046", text)
                    self.assertEqual(text.count("100%"), 1 if charge_seconds is not None else 0)
                    self.assertNotIn("\n\n", text)

    async def test_full_or_idle_battery_never_shows_a_hypothetical_runtime(self):
        cases = (
            InverterReading(232.8, True, 100, 5, -2, 200, 20, battery_power_watts=-108),
            InverterReading(232.8, True, 100, 500, -2, 200, 20, battery_power_watts=-108),
            InverterReading(232.8, True, 100, 5, 0, 200, 20, battery_power_watts=0),
            InverterReading(232.8, True, 100, 500, 0, 200, 20, battery_power_watts=0),
            InverterReading(232.8, True, 80, 5, 1, 200, 20, battery_power_watts=50),
        )
        for reading in cases:
            with self.subTest(reading=reading):
                wait = SimpleNamespace(edit_text=AsyncMock())
                message = SimpleNamespace(chat=SimpleNamespace(id=123), answer=AsyncMock(return_value=wait))
                monitor = SimpleNamespace(
                    check=AsyncMock(return_value=reading),
                    last_status_time=datetime.now(timezone.utc),
                    charging_estimate=lambda: ChargingEstimate(False, None),
                )
                with patch.object(chat_repository, "get_chat_language", AsyncMock(return_value="en")):
                    await handlers.cmd_status(
                        message,
                        None,
                        SimpleNamespace(next_event_for_state=lambda _: ("", "")),
                        monitor,
                        self.settings,
                    )
                text = wait.edit_text.await_args.args[0]
                self.assertNotIn("Estimated runtime", text)
                self.assertNotIn("Usable reserve", text)
                self.assertNotIn("To 100%", text)
                self.assertIn(f"Load: {reading.load_watts} W", text)
                if reading.battery_power_watts < 0:
                    self.assertIn("Charging: 108 W", text)

    async def test_real_discharge_on_grid_still_shows_measured_runtime(self):
        wait = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(chat=SimpleNamespace(id=123), answer=AsyncMock(return_value=wait))
        monitor = SimpleNamespace(
            check=AsyncMock(
                return_value=InverterReading(230, True, 80, 500, 10, 200, 20, battery_power_watts=500)
            ),
            last_status_time=datetime.now(timezone.utc),
            charging_estimate=lambda: ChargingEstimate(False, None),
        )
        with patch.object(chat_repository, "get_chat_language", AsyncMock(return_value="en")):
            await handlers.cmd_status(
                message,
                None,
                SimpleNamespace(next_event_for_state=lambda _: ("", "")),
                monitor,
                self.settings,
            )
        text = wait.edit_text.await_args.args[0]
        self.assertIn("Estimated runtime to 20%: 12h", text)
        self.assertNotIn("Charging:", text)
