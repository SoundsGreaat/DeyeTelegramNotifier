import unittest
from unittest.mock import Mock, call, patch

from support import IsolatedTestConfig

from deye_notifier.inverter.client import InverterClient, InverterReading


class InverterClientTests(IsolatedTestConfig, unittest.TestCase):
    def registers(self):
        values = [0] * 45
        values[0], values[14], values[28], values[34] = 2300, 300, 750, 80
        values[40], values[41], values[44] = 65536 - 4046, 65536 - 8000, 1
        settings = [0] * 14
        settings[0], settings[9], settings[13] = 200, 1, 15
        return values, settings, [1234, 1]

    def test_registers_decode_charge_current_and_read_capacity_cutoff_and_counter(self):
        connection = Mock()
        connection.read_holding_registers.side_effect = self.registers()
        with patch("deye_notifier.inverter.client.PySolarmanV5", return_value=connection) as client:
            snapshot = InverterClient(self.settings).read_status()
        self.assertEqual(snapshot, InverterReading(230, True, 80, 750, -80, 200, 15, 6677, -4046))
        self.assertEqual(client.call_args.args, (self.settings.inverter_ip, self.settings.logger_serial))
        self.assertEqual(
            connection.read_holding_registers.call_args_list, [call(150, 45), call(204, 14), call(85, 2)]
        )
        self.assertTrue(snapshot.is_grid_online)  # Register 164 is current, not mode 300.
        self.assertEqual(snapshot.battery_seconds(100), 1800)
        self.assertEqual(snapshot.charge_power_watts, 4046)
        connection.disconnect.assert_called_once()

    def test_optional_register_failure_keeps_status_and_uses_fallbacks(self):
        connection = Mock()
        connection.read_holding_registers.side_effect = [self.registers()[0], OSError(), OSError()]
        with patch("deye_notifier.inverter.client.PySolarmanV5", return_value=connection):
            snapshot = InverterClient(self.settings).read_status()
        self.assertEqual(snapshot.cutoff_soc(self.settings), 20)
        self.assertIsNone(snapshot.battery_capacity_ah)
        self.assertIsNone(snapshot.total_load_energy_kwh)
        self.assertTrue(snapshot.is_grid_online)

    def test_voltage_controlled_battery_does_not_use_soc_shutdown(self):
        values, settings, energy = self.registers()
        settings[9] = 0
        connection = Mock()
        connection.read_holding_registers.side_effect = [values, settings, energy]
        with patch("deye_notifier.inverter.client.PySolarmanV5", return_value=connection):
            snapshot = InverterClient(self.settings).read_status()
        self.assertIsNone(snapshot.shutdown_soc)

    def test_measured_runtime_includes_dc_consumption_without_manual_kwh(self):
        snapshot = InverterReading(0, False, 70, 390, 8, 200, 20)
        self.assertEqual(snapshot.runtime_seconds(self.settings), 45000)
        self.assertFalse(snapshot.is_grid_online)
        self.assertEqual(InverterReading(230, False, 70, 0).is_grid_online, False)
        self.assertTrue(InverterReading(230, None, 70, 0).is_grid_online)
        self.assertEqual(InverterReading(0, False, 10, 0, 8, 200, 20).runtime_seconds(self.settings), 0)
        self.assertIsNone(InverterReading(0, False, 70, 0, 0, 200).runtime_seconds(self.settings))

    def test_failed_read_still_disconnects(self):
        connection = Mock()
        connection.read_holding_registers.side_effect = OSError("synthetic read failure")
        with patch("deye_notifier.inverter.client.PySolarmanV5", return_value=connection):
            self.assertIsNone(InverterClient(self.settings).read_status())
        connection.disconnect.assert_called_once()
