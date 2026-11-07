import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from support import IsolatedTestConfig

from deye_notifier.database import monitor as state_repository
from deye_notifier.database import telemetry
from deye_notifier.inverter.client import InverterReading
from deye_notifier.inverter.monitor import DeyeStatusMonitor


class MonitorTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(telemetry, "fetch_samples", AsyncMock(return_value=[])))

    def test_charging_crosses_all_milestones_and_full(self):
        self.assertEqual(
            DeyeStatusMonitor.battery_events(20, 100),
            [
                ("battery_charging", 25),
                ("battery_charging", 50),
                ("battery_charging", 75),
                ("battery_full", 100),
            ],
        )

    def test_discharge_jump_does_not_skip_low_alert(self):
        self.assertEqual(
            DeyeStatusMonitor.battery_events(95, 20),
            [
                ("battery_discharging", 75),
                ("battery_discharging", 50),
                ("battery_low", 25),
            ],
        )
        self.assertEqual(DeyeStatusMonitor.battery_events(None, 20), [])
        self.assertEqual(DeyeStatusMonitor.battery_events(50, 50), [])

    async def test_saved_state_duration_survives_restart_and_charging_is_queued(self):
        monitor = DeyeStatusMonitor(
            Mock(), SimpleNamespace(next_event_for_state=lambda _: ("", "")), self.settings
        )
        since = datetime.now(timezone.utc) - timedelta(hours=3)
        monitor.read_inverter = Mock(return_value=InverterReading(230, True, 24, 1000))
        monitor.broadcast = AsyncMock()
        with (
            patch.object(
                state_repository,
                "load_monitor_state",
                AsyncMock(
                    return_value={
                        "is_grid_online": True,
                        "last_status_time": since,
                    }
                ),
            ),
            patch.object(telemetry, "record_sample", AsyncMock()) as record,
            patch.object(state_repository, "record_transition", AsyncMock()) as transition,
        ):
            await monitor.check()
            self.assertEqual(monitor.last_status_time, since)
            monitor.broadcast.assert_not_awaited()
            monitor.read_inverter.return_value = InverterReading(230, True, 51, 1000)
            await monitor.check()
            self.assertEqual(monitor.last_status_time, since)
            transition.assert_not_awaited()
            self.assertEqual(record.await_count, 2)
            self.assertEqual([call.kwargs["threshold"] for call in monitor.broadcast.await_args_list], [50])

    async def test_soc_jump_sends_one_current_level_notification(self):
        monitor = DeyeStatusMonitor(None, None, self.settings)
        monitor.is_grid_online = True
        monitor.last_battery_soc = 20
        monitor.read_inverter = Mock(return_value=InverterReading(230, True, 100, 1000))
        monitor.broadcast = AsyncMock()
        with patch.object(telemetry, "record_sample", AsyncMock()):
            await monitor.check()
        monitor.broadcast.assert_awaited_once_with(
            "battery_full",
            level=100,
            threshold=100,
            load=1000,
            charge_eta_seconds=None,
            charge_power_watts=None,
            runtime_eta_seconds=None,
            battery_cutoff_soc=20,
        )

    async def test_transition_is_persisted_before_new_duration(self):
        monitor = DeyeStatusMonitor(
            Mock(), SimpleNamespace(next_event_for_state=lambda _: ("", "")), self.settings
        )
        monitor.is_grid_online = True
        monitor.last_battery_soc = 60
        monitor.last_status_time = datetime.now(timezone.utc) - timedelta(hours=1)
        monitor.read_inverter = Mock(return_value=InverterReading(0, False, 60, 600))
        with (
            patch.object(telemetry, "record_sample", AsyncMock()),
            patch.object(state_repository, "record_transition", AsyncMock()) as transition,
        ):
            result = await monitor.check()
            self.assertEqual(result, InverterReading(0, False, 60, 600))
            self.assertFalse(monitor.is_grid_online)
            self.assertEqual(monitor.last_status_time, transition.await_args.args[2])
            self.assertEqual(transition.await_args.kwargs["battery_soc"], 60)

    async def test_saved_soc_history_restores_charge_estimate_after_restart(self):
        now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        history = [
            {"observed_at": now - timedelta(minutes=5 - index), "battery_soc": 70 + index}
            for index in range(5)
        ]
        monitor = DeyeStatusMonitor(None, None, self.settings)
        with patch.object(telemetry, "fetch_samples", AsyncMock(return_value=history)) as fetch:
            await monitor._observe_charge(now, 75)
            forecast = monitor.charge_estimator.estimate(now)
            self.assertTrue(forecast.is_charging)
            self.assertAlmostEqual(forecast.seconds_remaining, 25 * 60)
            await monitor._observe_charge(now + timedelta(seconds=10), 75)
        fetch.assert_awaited_once()

    async def test_charging_event_captures_current_load_and_eta(self):
        now = datetime.now(timezone.utc)
        monitor = DeyeStatusMonitor(None, None, self.settings)
        monitor.is_grid_online = True
        monitor.last_battery_soc = 74
        monitor._charge_history_loaded = True
        for offset, soc in ((150, 72), (100, 73), (50, 74)):
            monitor.charge_estimator.observe(now - timedelta(seconds=offset), soc)
        monitor.read_inverter = Mock(
            return_value=InverterReading(230, True, 75, 1000, battery_power_watts=-4046)
        )
        monitor.broadcast = AsyncMock()
        with patch.object(telemetry, "record_sample", AsyncMock()):
            await monitor.check()
        event = monitor.broadcast.await_args
        self.assertEqual(event.args[0], "battery_charging")
        self.assertEqual(event.kwargs["load"], 1000)
        self.assertEqual(event.kwargs["charge_power_watts"], 4046)
        self.assertAlmostEqual(event.kwargs["charge_eta_seconds"], 25 * 50, delta=1)

    async def test_measured_charge_eta_updates_immediately_as_current_changes(self):
        monitor = DeyeStatusMonitor(None, None, self.settings)
        monitor.is_grid_online = True
        monitor.read_inverter = Mock(return_value=InverterReading(230, True, 80, 1000, -80, 200, 15))
        with patch.object(telemetry, "record_sample", AsyncMock()) as record:
            await monitor.check()
            self.assertEqual(monitor.charging_estimate().seconds_remaining, 1800)
            monitor.read_inverter.return_value = InverterReading(230, True, 80, 1000, -40, 200, 15, 123.4)
            await monitor.check()
            self.assertEqual(monitor.charging_estimate().seconds_remaining, 3600)
            self.assertEqual(record.await_args.kwargs["total_load_energy_kwh"], 123.4)
            monitor.read_inverter.return_value = InverterReading(230, True, 80, 1000, 0, 200, 15)
            await monitor.check()
            self.assertFalse(monitor.charging_estimate().is_charging)

    async def test_runtime_event_retains_measured_cutoff_and_dc_consumption(self):
        monitor = DeyeStatusMonitor(None, None, self.settings)
        monitor.is_grid_online = False
        monitor.last_battery_soc = 76
        monitor.read_inverter = Mock(return_value=InverterReading(0, False, 75, 500, 10, 200, 15))
        monitor.broadcast = AsyncMock()
        with patch.object(telemetry, "record_sample", AsyncMock()):
            await monitor.check()
        self.assertEqual(monitor.broadcast.await_args.kwargs["runtime_eta_seconds"], 43200)
        self.assertEqual(monitor.broadcast.await_args.kwargs["battery_cutoff_soc"], 15)

    def outage_monitor(self, readings):
        monitor = DeyeStatusMonitor(
            None, SimpleNamespace(next_event_for_state=lambda _: ("", "")), self.settings
        )
        monitor.is_grid_online = True
        monitor.last_battery_soc = 70
        monitor.read_inverter = Mock(side_effect=readings)
        monitor.broadcast = AsyncMock()
        self.addAsyncCleanup(monitor.close)
        return monitor

    async def test_zero_load_outage_gets_one_fresh_followup_after_five_seconds(self):
        monitor = self.outage_monitor(
            [
                InverterReading(0, False, 70, 0),
                InverterReading(0, False, 70, 500, 10, 200, 20),
                InverterReading(0, False, 70, 600, 12, 200, 20),
            ]
        )
        with (
            patch.object(telemetry, "record_sample", AsyncMock()),
            patch.object(state_repository, "record_transition", AsyncMock()) as transition,
            patch("deye_notifier.inverter.monitor.asyncio.sleep", AsyncMock()) as delay,
        ):
            first = await monitor.check()
            monitor.broadcast.assert_not_awaited()
            self.assertEqual(first.load_watts, 0)
            await monitor._load_followup_task
            await monitor.check()  # Later normal polls must not repeat the update.
        delay.assert_awaited_once_with(5)
        transition.assert_awaited_once()
        monitor.broadcast.assert_awaited_once_with(
            "outage_load", load=500, level=70, runtime_eta_seconds=36000, battery_cutoff_soc=20
        )
        self.assertIsNone(monitor._load_followup_task)

    async def test_followup_does_not_send_zero_failed_or_restored_readings(self):
        for reading in (InverterReading(0, False, 70, 0), None, InverterReading(230, True, 70, 500)):
            with self.subTest(reading=reading):
                monitor = self.outage_monitor([InverterReading(0, False, 70, 0), reading])
                with (
                    patch.object(telemetry, "record_sample", AsyncMock()),
                    patch.object(state_repository, "record_transition", AsyncMock()),
                    patch("deye_notifier.inverter.monitor.asyncio.sleep", AsyncMock()) as delay,
                ):
                    await monitor.check()
                    await monitor._load_followup_task
                delay.assert_awaited_once_with(5)
                monitor.broadcast.assert_not_awaited()
                self.assertIsNone(monitor._load_followup_task)
                self.assertEqual(monitor.read_inverter.call_count, 2)

    async def test_restoration_before_timeout_cancels_the_pending_read(self):
        monitor = self.outage_monitor([InverterReading(0, False, 70, 0), InverterReading(230, True, 70, 500)])
        waiting = asyncio.Event()
        release = asyncio.Event()

        async def wait_delay(seconds):
            self.assertEqual(seconds, 5)
            waiting.set()
            await release.wait()

        with (
            patch.object(telemetry, "record_sample", AsyncMock()),
            patch.object(state_repository, "record_transition", AsyncMock()),
            patch("deye_notifier.inverter.monitor.asyncio.sleep", wait_delay),
        ):
            await monitor.check()
            await waiting.wait()
            await monitor.check()
        self.assertIsNone(monitor._load_followup_task)
        self.assertEqual(monitor.read_inverter.call_count, 2)
        monitor.broadcast.assert_not_awaited()

    async def test_nonzero_load_outage_does_not_schedule_a_followup(self):
        monitor = self.outage_monitor([InverterReading(0, False, 70, 500)])
        with (
            patch.object(telemetry, "record_sample", AsyncMock()),
            patch.object(state_repository, "record_transition", AsyncMock()),
        ):
            await monitor.check()
        self.assertIsNone(monitor._load_followup_task)
        monitor.broadcast.assert_not_awaited()

    async def test_shutdown_cancels_the_delayed_task_before_closing_resources(self):
        monitor = self.outage_monitor([InverterReading(0, False, 70, 0)])
        with (
            patch.object(telemetry, "record_sample", AsyncMock()),
            patch.object(state_repository, "record_transition", AsyncMock()),
        ):
            await monitor.check()
            task = monitor._load_followup_task
            await monitor.close()
        self.assertTrue(task.done())
        self.assertIsNone(monitor._load_followup_task)
        self.assertEqual(monitor.read_inverter.call_count, 1)

    async def test_discharge_milestone_during_followup_does_not_duplicate_load_update(self):
        monitor = self.outage_monitor([InverterReading(0, False, 76, 0), InverterReading(0, False, 75, 500)])
        monitor.last_battery_soc = 76
        with (
            patch.object(telemetry, "record_sample", AsyncMock()),
            patch.object(state_repository, "record_transition", AsyncMock()),
            patch("deye_notifier.inverter.monitor.asyncio.sleep", AsyncMock()),
        ):
            await monitor.check()
            await monitor._load_followup_task
        monitor.broadcast.assert_awaited_once()
        self.assertEqual(monitor.broadcast.await_args.args[0], "battery_discharging")
        self.assertEqual(monitor.broadcast.await_args.kwargs["load"], 500)

    async def test_discharge_milestone_before_timeout_cancels_duplicate_load_update(self):
        monitor = self.outage_monitor([InverterReading(0, False, 76, 0), InverterReading(0, False, 75, 500)])
        monitor.last_battery_soc = 76
        waiting = asyncio.Event()
        release = asyncio.Event()

        async def wait_delay(seconds):
            waiting.set()
            await release.wait()

        with (
            patch.object(telemetry, "record_sample", AsyncMock()),
            patch.object(state_repository, "record_transition", AsyncMock()),
            patch("deye_notifier.inverter.monitor.asyncio.sleep", wait_delay),
        ):
            await monitor.check()
            await waiting.wait()
            await monitor.check()  # A manual/short-interval poll already sends the settled load.
        self.assertIsNone(monitor._load_followup_task)
        self.assertEqual(monitor.read_inverter.call_count, 2)
        monitor.broadcast.assert_awaited_once()
        self.assertEqual(monitor.broadcast.await_args.args[0], "battery_discharging")
        self.assertEqual(monitor.broadcast.await_args.kwargs["load"], 500)

    async def test_configured_minimum_controls_initial_outage_and_delayed_load(self):
        for minimum, initial, settled, expected in (
            (10, 5, 9, False),
            (10, 5, 10, True),
            (30, 15, 29, False),
            (30, 15, 30, True),
        ):
            with self.subTest(minimum=minimum, initial=initial, settled=settled):
                monitor = self.outage_monitor(
                    [InverterReading(0, False, 70, initial), InverterReading(0, False, 70, settled)]
                )
                monitor.settings = replace(self.settings, min_load_watts=minimum)
                with (
                    patch.object(telemetry, "record_sample", AsyncMock()),
                    patch.object(state_repository, "record_transition", AsyncMock()),
                    patch("deye_notifier.inverter.monitor.asyncio.sleep", AsyncMock()) as delay,
                ):
                    await monitor.check()
                    await monitor._load_followup_task
                delay.assert_awaited_once_with(5)
                self.assertEqual(monitor.broadcast.await_count, int(expected))
                if expected:
                    self.assertEqual(monitor.broadcast.await_args.args[0], "outage_load")
                    self.assertEqual(monitor.broadcast.await_args.kwargs["load"], settled)
        monitor = self.outage_monitor([InverterReading(0, False, 70, 10)])
        with (
            patch.object(telemetry, "record_sample", AsyncMock()),
            patch.object(state_repository, "record_transition", AsyncMock()),
        ):
            await monitor.check()
        self.assertIsNone(monitor._load_followup_task)

    async def test_fully_charged_battery_still_recognizes_net_charging_without_zero_eta(self):
        monitor = self.outage_monitor(
            [InverterReading(230, True, 100, 5, -2, 200, 20, battery_power_watts=-108)]
        )
        with patch.object(telemetry, "record_sample", AsyncMock()):
            await monitor.check()
        estimate = monitor.charging_estimate()
        self.assertTrue(estimate.is_charging)
        self.assertIsNone(estimate.seconds_remaining)
        self.assertIsNone(monitor.latest_reading.runtime_seconds(self.settings))

    async def test_voltage_protection_and_relay_delay_do_not_change_grid_history(self):
        monitor = self.outage_monitor(
            [
                InverterReading(280, False, 70, 500),
                InverterReading(270, False, 70, 500),
                InverterReading(230, False, 70, 500),
                InverterReading(230, True, 70, 500),
            ]
        )
        since = monitor.last_status_time
        with (
            patch.object(telemetry, "record_sample", AsyncMock()) as record,
            patch.object(state_repository, "record_transition", AsyncMock()) as transition,
        ):
            for _ in range(4):
                await monitor.check()
        self.assertTrue(monitor.is_grid_online)
        self.assertEqual(monitor.last_status_time, since)
        transition.assert_not_awaited()
        self.assertTrue(all(call.args[2] is True for call in record.await_args_list))
        self.assertEqual(
            [call.args[0] for call in monitor.broadcast.await_args_list],
            ["grid_voltage_high", "grid_voltage_normal", "grid_reconnected"],
        )
        self.assertIsNone(monitor._load_followup_task)

    async def test_grid_restoration_is_recorded_before_the_relay_reconnects(self):
        monitor = self.outage_monitor(
            [
                InverterReading(230, False, 70, 500),
                InverterReading(230, False, 70, 500),
                InverterReading(230, True, 70, 500),
            ]
        )
        monitor.is_grid_online = False
        with (
            patch.object(telemetry, "record_sample", AsyncMock()),
            patch.object(state_repository, "record_transition", AsyncMock()) as transition,
        ):
            await monitor.check()
            transition.assert_awaited_once()
            self.assertIs(transition.await_args.args[1], True)
            self.assertTrue(monitor.is_grid_online)
            monitor.broadcast.assert_not_awaited()
            await monitor.check()
            monitor.broadcast.assert_not_awaited()
            await monitor.check()
        transition.assert_awaited_once()
        monitor.broadcast.assert_awaited_once_with("grid_reconnected", voltage=230)

    async def test_first_abnormal_reading_alerts_but_normal_startup_is_quiet(self):
        for voltage, connected, expected in (
            (230, True, None),
            (280, False, "grid_voltage_high"),
            (150, False, "grid_voltage_low"),
            (230, False, "grid_disconnected"),
        ):
            with self.subTest(voltage=voltage, connected=connected):
                monitor = self.outage_monitor([InverterReading(voltage, connected, 70, 500)])
                monitor.is_grid_online = None
                with (
                    patch.object(state_repository, "load_monitor_state", AsyncMock(return_value=None)),
                    patch.object(state_repository, "save_initial_monitor_state", AsyncMock()),
                    patch.object(telemetry, "record_sample", AsyncMock()),
                ):
                    await monitor.check()
                if expected is None:
                    monitor.broadcast.assert_not_awaited()
                elif expected == "grid_disconnected":
                    monitor.broadcast.assert_awaited_once_with(
                        expected,
                        voltage=voltage,
                        load=500,
                        level=70,
                        runtime_eta_seconds=None,
                        battery_cutoff_soc=20,
                    )
                else:
                    monitor.broadcast.assert_awaited_once_with(expected, voltage=voltage)

    async def test_corrupt_voltage_never_becomes_an_outage_or_a_fresh_reading(self):
        for voltage in (float("nan"), float("inf"), -1, 6553.5):
            with self.subTest(voltage=voltage):
                monitor = self.outage_monitor([InverterReading(voltage, False, 70, 500)])
                with (
                    patch.object(telemetry, "record_sample", AsyncMock()) as record,
                    patch.object(state_repository, "record_transition", AsyncMock()) as transition,
                ):
                    self.assertIsNone(await monitor.check())
                transition.assert_not_awaited()
                record.assert_not_awaited()
                monitor.broadcast.assert_not_awaited()
                self.assertTrue(monitor.is_grid_online)
                self.assertIsNone(monitor.latest_reading)

    async def test_voltage_alert_is_retried_if_its_durable_enqueue_fails(self):
        monitor = self.outage_monitor([InverterReading(280, False, 70, 500)] * 2)
        monitor.broadcast.side_effect = [RuntimeError("synthetic enqueue failure"), None]
        with patch.object(telemetry, "record_sample", AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "synthetic enqueue failure"):
                await monitor.check()
            await monitor.check()
        self.assertEqual(monitor.broadcast.await_count, 2)
        self.assertTrue(
            all(call.args[0] == "grid_voltage_high" for call in monitor.broadcast.await_args_list)
        )
