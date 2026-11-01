"""Read-only Solarman V5 telemetry for single-phase Deye hybrids."""

import logging
import math
from dataclasses import dataclass

from pysolarmanv5 import PySolarmanV5

GRID_V_REG = 150
VOLTAGE_THRESHOLD = 100
logger = logging.getLogger(__name__)


def signed_word(value):
    return value - 65536 if value >= 32768 else value


@dataclass(frozen=True)
class InverterReading:
    grid_voltage: float
    grid_connected: bool | None
    battery_soc: int
    load_watts: int
    battery_current: float | None = None
    battery_capacity_ah: float | None = None
    shutdown_soc: float | None = None
    total_load_energy_kwh: float | None = None
    battery_power_watts: int | None = None

    @property
    def is_charging(self):
        if self.battery_power_watts is not None:
            return self.battery_power_watts < 0
        return self.battery_current is not None and self.battery_current < -0.1

    @property
    def is_discharging(self):
        if self.battery_power_watts is not None:
            return self.battery_power_watts > 0
        return self.battery_current is not None and self.battery_current > 0.1

    @property
    def charge_power_watts(self):
        # Deye reports negative DC power while charging.
        power = self.battery_power_watts
        return -power if power is not None and power < 0 else None

    @property
    def is_grid_online(self):
        return (
            self.grid_connected if self.grid_connected is not None else self.grid_voltage > VOLTAGE_THRESHOLD
        )

    def cutoff_soc(self, settings):
        return self.shutdown_soc if self.shutdown_soc is not None else settings.battery_cutoff_soc

    def battery_seconds(self, target_soc):
        """DC current includes inverter consumption; no guessed AC efficiency needed."""
        capacity, current = self.battery_capacity_ah, self.battery_current
        if capacity is None or current is None or not math.isfinite(current) or abs(current) < 0.1:
            return None
        delta = self.battery_soc - target_soc
        if delta * current < 0:
            return None
        return abs(delta) / 100 * capacity / abs(current) * 3600

    def runtime_seconds(self, settings):
        if not self.is_discharging:
            return None
        return self.battery_seconds(min(self.battery_soc, self.cutoff_soc(settings)))


class InverterClient:
    def __init__(self, settings):
        self.settings = settings

    def read_status(self):
        inv = None
        try:
            inv = PySolarmanV5(
                self.settings.inverter_ip, self.settings.logger_serial, socket_timeout=5, verbose=False
            )
            data = inv.read_holding_registers(GRID_V_REG, 45)
            current = signed_word(data[41]) / 100.0
            current = current if abs(current) <= 1000 else None
            capacity = cutoff = total_energy = None
            # Optional settings/counters must not prevent live status if unsupported.
            try:
                settings = inv.read_holding_registers(204, 14)
                capacity = settings[0] if 0 < settings[0] <= 2000 else None
                if settings[9] == 1 and 0 <= settings[13] <= 100:
                    cutoff = settings[13]
            except Exception:
                logger.warning("Battery settings unavailable; using configured fallbacks")
            try:
                energy = inv.read_holding_registers(85, 2)
                total_energy = (energy[0] + (energy[1] << 16)) / 10.0
            except Exception:
                logger.warning("Load energy counter unavailable; using sampled load")
            return InverterReading(
                grid_voltage=data[0] / 10.0,
                grid_connected=bool(data[44]) if data[44] in (0, 1) else None,
                battery_soc=data[34],
                load_watts=signed_word(data[28]),
                battery_current=current,
                battery_capacity_ah=capacity,
                shutdown_soc=cutoff,
                total_load_energy_kwh=total_energy,
                battery_power_watts=signed_word(data[40]),
            )
        except Exception:
            logger.exception("Could not read inverter telemetry")
            return None
        finally:
            if inv:
                try:
                    inv.disconnect()
                except Exception:
                    pass
