"""Separate utility voltage presence from the inverter's grid relay."""

import math
from dataclasses import dataclass

VOLTAGE_THRESHOLD = 100


def valid_grid_voltage(value):
    """Reject corrupt samples; zero is a valid observation of an absent grid."""
    return isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 500


@dataclass(frozen=True)
class GridAlertState:
    voltage_state: str | None = None
    disconnected: bool | None = None

    def observe(self, reading, settings, *, suppress_connection_events=False):
        """Return a proposed state and at most one alert; commit after enqueue."""
        if not reading.is_grid_online:
            return GridAlertState(), None

        voltage = reading.grid_voltage
        minimum, maximum = settings.grid_voltage_min, settings.grid_voltage_max
        hysteresis = settings.grid_voltage_hysteresis
        if voltage < minimum:
            voltage_state = "low"
        elif voltage > maximum:
            voltage_state = "high"
        elif self.voltage_state == "low" and voltage < minimum + hysteresis:
            voltage_state = "low"
        elif self.voltage_state == "high" and voltage > maximum - hysteresis:
            voltage_state = "high"
        else:
            voltage_state = "normal"

        connected = reading.grid_connected
        disconnected = not connected if connected is not None else self.disconnected
        next_state = GridAlertState(voltage_state, disconnected)
        event = None
        if voltage_state in {"high", "low"}:
            if voltage_state != self.voltage_state:
                event = f"grid_voltage_{voltage_state}"
        elif connected is False and (self.disconnected is not True or self.voltage_state in {"high", "low"}):
            event = "grid_disconnected"
        elif connected is True and self.disconnected is True:
            event = "grid_reconnected"
        elif self.voltage_state in {"high", "low"}:
            event = "grid_voltage_normal"

        if suppress_connection_events and event in {"grid_disconnected", "grid_reconnected"}:
            event = None
        return next_state, event
