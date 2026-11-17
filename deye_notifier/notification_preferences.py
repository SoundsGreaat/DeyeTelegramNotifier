"""Keys shared by notification delivery and chat preferences."""

ALERT_KEYS = (
    "warning_battery",
    "power_restored",
    "outage_load",
    "grid_voltage_high",
    "grid_voltage_low",
    "grid_voltage_normal",
    "grid_disconnected",
    "grid_reconnected",
    "battery_charging",
    "battery_discharging",
    "battery_low",
    "battery_full",
)
REPORT_KEYS = {"day": "report_day", "week": "report_week", "month": "report_month"}
PREFERENCE_KEYS = (*ALERT_KEYS, *REPORT_KEYS.values())
