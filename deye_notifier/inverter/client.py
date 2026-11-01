"""Blocking Solarman V5 access; no network work is done during imports."""

import logging

from pysolarmanv5 import PySolarmanV5

GRID_V_REG = 150
VOLTAGE_THRESHOLD = 100
logger = logging.getLogger(__name__)


class InverterClient:
    def __init__(self, settings):
        self.settings = settings

    def read_status(self):
        inv = None
        try:
            inv = PySolarmanV5(
                self.settings.inverter_ip, self.settings.logger_serial, socket_timeout=5, verbose=False
            )
            data = inv.read_holding_registers(GRID_V_REG, 35)
            grid_v = data[0] / 10.0
            current_mode = data[14]
            battery_soc = data[34]
            load_watts = data[28]
            return grid_v, current_mode, battery_soc, load_watts
        except Exception as e:
            logger.error(f"Read error: {e}")
            return None, None, None, None
        finally:
            if inv:
                try:
                    inv.disconnect()
                except:
                    pass
