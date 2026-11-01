import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from support import IsolatedTestConfig

from deye_notifier.database import monitor as state_repository
from deye_notifier.database import telemetry
from deye_notifier.inverter.monitor import DeyeStatusMonitor


class MonitorTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
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
        monitor.read_inverter = Mock(return_value=(230, 0, 24, 1000))
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
            monitor.read_inverter.return_value = (230, 0, 51, 1000)
            await monitor.check()
            self.assertEqual(monitor.last_status_time, since)
            transition.assert_not_awaited()
            self.assertEqual(record.await_count, 2)
            self.assertEqual([call.kwargs["threshold"] for call in monitor.broadcast.await_args_list], [50])

    async def test_soc_jump_sends_one_current_level_notification(self):
        monitor = DeyeStatusMonitor(None, None, self.settings)
        monitor.is_grid_online = True
        monitor.last_battery_soc = 20
        monitor.read_inverter = Mock(return_value=(230, 0, 100, 1000))
        monitor.broadcast = AsyncMock()
        with patch.object(telemetry, "record_sample", AsyncMock()):
            await monitor.check()
        monitor.broadcast.assert_awaited_once_with("battery_full", level=100, threshold=100)

    async def test_transition_is_persisted_before_new_duration(self):
        monitor = DeyeStatusMonitor(
            Mock(), SimpleNamespace(next_event_for_state=lambda _: ("", "")), self.settings
        )
        monitor.is_grid_online = True
        monitor.last_battery_soc = 60
        monitor.last_status_time = datetime.now(timezone.utc) - timedelta(hours=1)
        monitor.read_inverter = Mock(return_value=(0, 300, 60, 600))
        with (
            patch.object(telemetry, "record_sample", AsyncMock()),
            patch.object(state_repository, "record_transition", AsyncMock()) as transition,
        ):
            result = await monitor.check()
            self.assertEqual(result, (0, 300, 60, 600))
            self.assertFalse(monitor.is_grid_online)
            self.assertEqual(monitor.last_status_time, transition.await_args.args[2])
