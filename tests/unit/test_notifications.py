import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from support import IsolatedTestConfig, make_stats

from deye_notifier.bot import locales
from deye_notifier.database import chats as chat_repository
from deye_notifier.database import notifications as notification_repository
from deye_notifier.services.notifications import NotificationService


class NotificationDeliveryTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.bot = SimpleNamespace(send_message=AsyncMock())

    async def test_voltage_and_relay_events_use_distinct_localized_messages(self):
        for language in locales.LANGUAGES:
            for key in (
                "grid_voltage_high",
                "grid_voltage_low",
                "grid_voltage_normal",
                "grid_disconnected",
                "grid_reconnected",
            ):
                with self.subTest(language=language, key=key):
                    event = dict(
                        id=1,
                        message_key=key,
                        voltage=280
                        if key == "grid_voltage_high"
                        else 150
                        if key == "grid_voltage_low"
                        else 230,
                        load_watts=500,
                        duration="",
                        level=70,
                        threshold=None,
                        forecast_action="",
                        forecast_text="",
                        report_payload=None,
                        runtime_eta_seconds=36000,
                        battery_cutoff_soc=20,
                    )
                    with (
                        patch.object(
                            notification_repository,
                            "get_pending_notifications",
                            AsyncMock(return_value=[event]),
                        ),
                        patch.object(
                            chat_repository, "get_chats", AsyncMock(return_value=[(123, language)])
                        ) as recipients,
                        patch.object(
                            notification_repository, "mark_notification_delivered", AsyncMock()
                        ) as mark,
                    ):
                        await NotificationService(None, self.bot, self.settings).deliver_pending()
                    text = self.bot.send_message.await_args.args[1]
                    recipients.assert_awaited_once_with(None, stats_only=False)
                    mark.assert_awaited_once_with(None, 1)
                    self.assertIn(locales.get_voltage_line(language, event["voltage"]).strip(), text)
                    self.assertNotIn(key, text)
                    self.assertNotIn("{", text)
                    self.assertNotIn(
                        locales.get_message(language, "warning_battery", duration="").splitlines()[0], text
                    )
                    self.assertNotIn(
                        locales.get_message(language, "power_restored", duration="").splitlines()[0], text
                    )
                    if key == "grid_disconnected":
                        self.assertIn("70%", text)
                        self.assertIn(locales.format_duration(36000, language), text)

    async def test_grid_disconnection_does_not_invent_runtime_when_not_discharging(self):
        event = dict(
            id=1,
            message_key="grid_disconnected",
            voltage=230,
            load_watts=500,
            duration="",
            level=70,
            threshold=None,
            forecast_action="",
            forecast_text="",
            report_payload=None,
            runtime_eta_seconds=None,
            battery_cutoff_soc=20,
        )
        with (
            patch.object(
                notification_repository, "get_pending_notifications", AsyncMock(return_value=[event])
            ),
            patch.object(chat_repository, "get_chats", AsyncMock(return_value=[(123, "en")])),
            patch.object(notification_repository, "mark_notification_delivered", AsyncMock()),
        ):
            await NotificationService(None, self.bot, self.settings).deliver_pending()
        text = self.bot.send_message.await_args.args[1]
        self.assertIn("Inverter disconnected from grid", text)
        self.assertIn("Load: 500 W", text)
        self.assertIn("70%", text)
        self.assertNotIn("Estimated runtime", text)

    async def test_battery_messages_include_the_appropriate_estimate(self):
        cases = (
            ("battery_charging", 1800, "To 100%: about 30m"),
            ("battery_discharging", None, "Estimated runtime to 20%: 4h 57m"),
            ("battery_low", None, "Estimated runtime to 20%: 4h 57m"),
            ("warning_battery", None, "Estimated runtime to 15%: 12h"),
            ("outage_load", None, "Load: 1000 W"),
            ("power_restored", None, "Grid was unavailable: 1h"),
        )
        for key, seconds, expected in cases:
            event = dict(
                id=1,
                message_key=key,
                voltage=230.1 if key == "power_restored" else 0,
                load_watts=1000,
                duration="1h",
                level=75,
                threshold=75,
                forecast_action="",
                forecast_text="",
                report_payload=None,
                charge_eta_seconds=seconds,
                charge_power_watts=4046 if key == "battery_charging" else None,
                runtime_eta_seconds=43200 if key == "warning_battery" else None,
                battery_cutoff_soc=15 if key == "warning_battery" else None,
            )
            with (
                self.subTest(message=key),
                patch.object(
                    notification_repository, "get_pending_notifications", AsyncMock(return_value=[event])
                ),
                patch.object(chat_repository, "get_chats", AsyncMock(return_value=[(123, "en")])),
                patch.object(notification_repository, "mark_notification_delivered", AsyncMock()),
            ):
                service = NotificationService(None, self.bot, self.settings)
                await service.deliver_pending()
            self.assertIn(expected, self.bot.send_message.await_args.args[1])
            if key == "battery_charging":
                self.assertIn("Charging: 4046 W", self.bot.send_message.await_args.args[1])
            text = self.bot.send_message.await_args.args[1]
            self.assertNotIn("Usable reserve", text)
            if key in {"battery_discharging", "battery_low", "warning_battery", "outage_load"}:
                self.assertIn("Load: 1000 W", text)
                self.assertLess(text.index("Load:"), text.index("Estimated runtime"))
            if key == "warning_battery":
                self.assertIn("Battery: 75%", text)
                self.assertNotIn("Voltage:", text)
                self.assertLess(text.index("Grid was available:"), text.index("Load:"))
            if key == "power_restored":
                self.assertIn("Voltage: 230.1 V", text)
                self.assertLess(text.index("Grid was unavailable:"), text.index("Voltage:"))

    async def test_report_delivery_formats_saved_payload_for_chat_language(self):
        monitor = NotificationService(None, self.bot, self.settings)
        event = dict(
            id=1,
            message_key="statistics",
            voltage=None,
            load_watts=0,
            duration="",
            level=None,
            threshold=None,
            forecast_action=None,
            forecast_text=None,
            report_payload=json.dumps(
                {
                    "period": "day",
                    "start": "2026-11-01T00:00:00+02:00",
                    "end": "2026-11-02T00:00:00+02:00",
                    "stats": make_stats(),
                }
            ),
        )
        with (
            patch.object(
                notification_repository, "get_pending_notifications", AsyncMock(return_value=[event])
            ),
            patch.object(chat_repository, "get_chats", AsyncMock(return_value=[(123, "uk")])) as recipients,
            patch.object(self.bot, "send_message", AsyncMock()) as send,
            patch.object(notification_repository, "mark_notification_delivered", AsyncMock()) as mark,
        ):
            await monitor.deliver_pending()
        self.assertIn("Денна", send.await_args.args[1])
        self.assertIn("3.50", send.await_args.args[1])
        recipients.assert_awaited_once_with(None, stats_only=True)
        mark.assert_awaited_once_with(None, 1)

    async def test_report_without_opted_in_recipients_is_not_sent(self):
        monitor = NotificationService(None, self.bot, self.settings)
        with (
            patch.object(
                notification_repository,
                "get_pending_notifications",
                AsyncMock(
                    return_value=[
                        {"id": 1, "message_key": "statistics"},
                    ]
                ),
            ),
            patch.object(chat_repository, "get_chats", AsyncMock(return_value=[])) as recipients,
            patch.object(self.bot, "send_message", AsyncMock()) as send,
            patch.object(notification_repository, "mark_notification_delivered", AsyncMock()) as mark,
        ):
            await monitor.deliver_pending()
        recipients.assert_awaited_once_with(None, stats_only=True)
        send.assert_not_awaited()
        mark.assert_awaited_once_with(None, 1)

    async def test_power_alerts_use_power_subscribers(self):
        monitor = NotificationService(None, self.bot, self.settings)
        event = dict(
            id=1,
            message_key="battery_full",
            voltage=None,
            load_watts=0,
            duration="",
            level=100,
            threshold=100,
            forecast_action=None,
            forecast_text=None,
            report_payload=None,
        )
        with (
            patch.object(
                notification_repository, "get_pending_notifications", AsyncMock(return_value=[event])
            ),
            patch.object(chat_repository, "get_chats", AsyncMock(return_value=[(123, "uk")])) as recipients,
            patch.object(self.bot, "send_message", AsyncMock()) as send,
            patch.object(notification_repository, "mark_notification_delivered", AsyncMock()),
        ):
            await monitor.deliver_pending()
        recipients.assert_awaited_once_with(None, stats_only=False)
        self.assertEqual(send.await_args.args[0], 123)

    async def test_disabled_global_reports_do_not_deliver_old_queue_events(self):
        monitor = NotificationService(None, self.bot, self.settings)
        monitor.settings = replace(self.settings, stats_auto_reports=False)
        with (
            patch.object(
                notification_repository,
                "get_pending_notifications",
                AsyncMock(
                    return_value=[
                        {"id": 1, "message_key": "statistics"},
                    ]
                ),
            ),
            patch.object(self.bot, "send_message", AsyncMock()) as send,
            patch.object(notification_repository, "mark_notification_delivered", AsyncMock()) as mark,
        ):
            await monitor.deliver_pending()
        send.assert_not_awaited()
        mark.assert_awaited_once_with(None, 1)

    async def test_zero_load_is_hidden_only_in_the_initial_outage_message_in_all_languages(self):
        from deye_notifier.bot import locales

        for language in locales.LANGUAGES:
            event = dict(
                id=1,
                message_key="warning_battery",
                voltage=220.7,
                load_watts=0,
                duration="22m 11s",
                level=70,
                threshold=None,
                forecast_action="turn_on",
                forecast_text="01.11.2026 20:00",
                report_payload=None,
                runtime_eta_seconds=36000,
                battery_cutoff_soc=20,
            )
            with (
                self.subTest(language=language),
                patch.object(
                    notification_repository, "get_pending_notifications", AsyncMock(return_value=[event])
                ),
                patch.object(chat_repository, "get_chats", AsyncMock(return_value=[(123, language)])),
                patch.object(notification_repository, "mark_notification_delivered", AsyncMock()),
            ):
                await NotificationService(None, self.bot, self.settings).deliver_pending()
            text = self.bot.send_message.await_args.args[1]
            self.assertNotIn(locales.get_load_line(language, 0), text)
            self.assertNotIn("220.7", text)
            self.assertNotIn(locales.format_duration(36000, language), text)
            self.assertIn("70%", text)
            self.assertIn(locales.format_duration(1331, language), text)
            self.assertIn("01.11.2026 20:00", text)

    async def test_minimum_load_filters_small_outage_updates_and_runtime_lines(self):
        for key, watts, minimum, show in (
            ("warning_battery", 5, 10, False),
            ("warning_battery", 10, 10, True),
            ("battery_discharging", 5, 10, False),
            ("outage_load", 5, 10, False),
            ("warning_battery", 15, 20, False),
            ("outage_load", 20, 20, True),
        ):
            with self.subTest(key=key, watts=watts, minimum=minimum):
                self.bot.send_message.reset_mock()
                event = dict(
                    id=1,
                    message_key=key,
                    voltage=0,
                    load_watts=watts,
                    duration="1h",
                    level=70,
                    threshold=None,
                    forecast_action="",
                    forecast_text="",
                    report_payload=None,
                    runtime_eta_seconds=36000,
                    battery_cutoff_soc=20,
                )
                with (
                    patch.object(
                        notification_repository, "get_pending_notifications", AsyncMock(return_value=[event])
                    ),
                    patch.object(chat_repository, "get_chats", AsyncMock(return_value=[(123, "en")])),
                    patch.object(notification_repository, "mark_notification_delivered", AsyncMock()) as mark,
                ):
                    await NotificationService(
                        None, self.bot, replace(self.settings, min_load_watts=minimum)
                    ).deliver_pending()
                mark.assert_awaited_once_with(None, 1)
                if key == "outage_load" and not show:
                    self.bot.send_message.assert_not_awaited()
                    continue
                text = self.bot.send_message.await_args.args[1]
                self.assertEqual(f"Load: {watts} W" in text, show)
                self.assertEqual("Estimated runtime" in text, show)
