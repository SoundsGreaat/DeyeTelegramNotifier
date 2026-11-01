import unittest
from unittest.mock import Mock, patch

from support import IsolatedTestConfig

from deye_notifier.inverter.client import InverterClient


class InverterClientTests(IsolatedTestConfig, unittest.TestCase):
    def test_registers_are_read_using_instance_settings(self):
        values = [0] * 35
        values[0], values[14], values[28], values[34] = 2300, 0, 750, 80
        connection = Mock()
        connection.read_holding_registers.return_value = values
        with patch("deye_notifier.inverter.client.PySolarmanV5", return_value=connection) as client:
            snapshot = InverterClient(self.settings).read_status()
        self.assertEqual(snapshot, (230, 0, 80, 750))
        self.assertEqual(client.call_args.args, (self.settings.inverter_ip, self.settings.logger_serial))
        connection.read_holding_registers.assert_called_once_with(150, 35)
        connection.disconnect.assert_called_once()

    def test_failed_read_still_disconnects(self):
        connection = Mock()
        connection.read_holding_registers.side_effect = OSError("synthetic read failure")
        with patch("deye_notifier.inverter.client.PySolarmanV5", return_value=connection):
            self.assertEqual(InverterClient(self.settings).read_status(), (None, None, None, None))
        connection.disconnect.assert_called_once()
