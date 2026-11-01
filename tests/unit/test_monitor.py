import unittest
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
        monitor.read_inverter = Mock(return_value=InverterReading(230, True, 75, 1000))
        monitor.broadcast = AsyncMock()
        with patch.object(telemetry, "record_sample", AsyncMock()):
            await monitor.check()
        event = monitor.broadcast.await_args
        self.assertEqual(event.args[0], "battery_charging")
        self.assertEqual(event.kwargs["load"], 1000)
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
