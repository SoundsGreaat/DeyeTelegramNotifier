import unittest
from types import SimpleNamespace

from deye_notifier.inverter.client import InverterReading
from deye_notifier.inverter.grid import GridAlertState


class GridAlertTests(unittest.TestCase):
    settings = SimpleNamespace(grid_voltage_min=180, grid_voltage_max=260, grid_voltage_hysteresis=5)

    def events(self, samples):
        state, events = GridAlertState(), []
        for voltage, connected in samples:
            state, event = state.observe(InverterReading(voltage, connected, 80, 500), self.settings)
            if event:
                events.append(event)
        return events

    def test_high_voltage_waiting_relay_and_actual_reconnection_are_distinct(self):
        self.assertEqual(
            self.events([(280, False), (270, False), (230, False), (230, False), (230, True)]),
            ["grid_voltage_high", "grid_disconnected", "grid_reconnected"],
        )

    def test_high_and_low_recovery_use_hysteresis_and_do_not_repeat_on_every_poll(self):
        self.assertEqual(
            self.events(
                [
                    (261, True),
                    (260, True),
                    (256, True),
                    (255, True),
                    (179, True),
                    (180, True),
                    (184, True),
                    (185, True),
                ]
            ),
            ["grid_voltage_high", "grid_voltage_normal", "grid_voltage_low", "grid_voltage_normal"],
        )

    def test_an_absent_grid_does_not_produce_a_low_voltage_or_recovery_alert(self):
        self.assertEqual(self.events([(230, True), (0, False), (4, False), (100, False), (230, True)]), [])

    def test_high_to_low_alerts_for_each_distinct_problem(self):
        self.assertEqual(self.events([(280, False), (150, False)]), ["grid_voltage_high", "grid_voltage_low"])

    def test_unknown_relay_does_not_invent_disconnects_or_reconnections(self):
        self.assertEqual(self.events([(230, None), (230, True), (230, None)]), [])
        self.assertEqual(
            self.events([(230, False), (230, None), (230, True)]),
            ["grid_disconnected", "grid_reconnected"],
        )

    def test_restoration_can_suppress_connection_notice_without_losing_reconnection(self):
        state, event = GridAlertState().observe(
            InverterReading(230, False, 80, 500), self.settings, suppress_connection_events=True
        )
        self.assertIsNone(event)
        _, event = state.observe(InverterReading(230, True, 80, 500), self.settings)
        self.assertEqual(event, "grid_reconnected")
        _, event = GridAlertState().observe(
            InverterReading(280, False, 80, 500), self.settings, suppress_connection_events=True
        )
        self.assertEqual(event, "grid_voltage_high")
