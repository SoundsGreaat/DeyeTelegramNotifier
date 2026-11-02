"""Pure status message rendering shared by live and pinned status replies."""

import math
from datetime import UTC
from html import escape

from deye_notifier.bot import locales

_DETAIL_LABELS = {
    "en": {
        "title": "System Status",
        "last_known": "Last known state",
        "current": "Battery current",
        "capacity": "Battery capacity",
        "cutoff": "Shutdown cutoff",
        "discharge": "DC discharge",
        "energy": "Total load energy",
        "updated": "Last reading",
        "grid": "Grid",
        "battery_voltage": "Battery voltage",
        "inverter_dc_temperature": "DC transformer temperature",
        "inverter_radiator_temperature": "Inverter radiator temperature",
        "battery_temperature": "Battery temperature",
    },
    "uk": {
        "title": "Статус системи",
        "last_known": "Останній відомий стан",
        "current": "Струм батареї",
        "capacity": "Ємність батареї",
        "cutoff": "Поріг вимкнення",
        "discharge": "Розряджання DC",
        "energy": "Загальне споживання",
        "updated": "Останнє зчитування",
        "grid": "Мережа",
        "battery_voltage": "Напруга батареї",
        "inverter_dc_temperature": "Температура DC-трансформатора",
        "inverter_radiator_temperature": "Температура радіатора інвертора",
        "battery_temperature": "Температура батарей",
    },
    "es": {
        "title": "Estado del sistema",
        "last_known": "Último estado conocido",
        "current": "Corriente de batería",
        "capacity": "Capacidad de batería",
        "cutoff": "Umbral de apagado",
        "discharge": "Descarga DC",
        "energy": "Consumo total",
        "updated": "Última lectura",
        "grid": "Red",
        "battery_voltage": "Voltaje de batería",
        "inverter_dc_temperature": "Temperatura del transformador DC",
        "inverter_radiator_temperature": "Temperatura del disipador del inversor",
        "battery_temperature": "Temperatura de batería",
    },
    "de": {
        "title": "Systemstatus",
        "last_known": "Zuletzt bekannter Zustand",
        "current": "Batteriestrom",
        "capacity": "Batteriekapazität",
        "cutoff": "Abschaltgrenze",
        "discharge": "DC-Entladung",
        "energy": "Gesamtverbrauch",
        "updated": "Letzte Messung",
        "grid": "Netz",
        "battery_voltage": "Batteriespannung",
        "inverter_dc_temperature": "Temperatur des DC-Transformators",
        "inverter_radiator_temperature": "Temperatur des Wechselrichterkühlkörpers",
        "battery_temperature": "Batterietemperatur",
    },
}


def _finite(value):
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _temperature_line(label, value):
    if _finite(value) and -100 < float(value) <= 200:
        return f"🌡️ {label}: {float(value):.1f} °C"
    return ""


def _is_stale(observed_at, settings, now):
    if observed_at is None:
        return False
    max_age = getattr(settings, "stats_max_gap_seconds", None)
    if max_age is None:
        return False
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return (now - observed_at).total_seconds() > max_age


def render_status(
    language,
    reading,
    settings,
    *,
    state_since,
    charge_estimate,
    now,
    forecast_action="",
    forecast_text="",
    detailed=False,
    observed_at=None,
):
    """Render the compact status or its expanded, timestamped counterpart."""
    is_online = reading.is_grid_online
    soc, load_watts = reading.battery_soc, reading.load_watts
    stale = bool(detailed and _is_stale(observed_at, settings, now))
    duration_end = observed_at if stale and observed_at is not None else now
    state_duration = (duration_end - state_since).total_seconds() if state_since is not None else None
    is_charging = reading.is_charging or bool(getattr(charge_estimate, "is_charging", False))
    show_runtime = (
        not stale
        and not is_charging
        and settings.has_meaningful_load(load_watts)
        and (not is_online or reading.is_discharging)
    )
    runtime_line = (
        locales.get_runtime_line(
            language,
            soc,
            load_watts,
            settings.battery_capacity_kwh,
            reading.cutoff_soc(settings),
            settings.battery_inverter_efficiency,
            runtime_seconds=reading.runtime_seconds(settings),
        )
        if show_runtime
        else ""
    )
    charge_power_line = locales.get_charge_power_line(language, reading.charge_power_watts)
    charge_time_line = (
        locales.get_charge_time_line(language, getattr(charge_estimate, "seconds_remaining", None))
        if not stale and is_charging and soc < 100
        else ""
    )

    if not detailed:
        forecast_line = locales.get_forecast_line(language, forecast_action, forecast_text)
        return locales.get_message(
            language,
            "status",
            grid_status=locales.get_message(language, "online" if is_online else "offline"),
            voltage=reading.grid_voltage,
            voltage_line=locales.get_voltage_line(language, reading.grid_voltage) if is_online else "",
            battery=soc,
            load=load_watts,
            state_duration_line=locales.get_state_duration_line(language, is_online, state_duration),
            runtime_line=runtime_line,
            charge_power_line=charge_power_line,
            charge_time_line=charge_time_line,
            forecast_line=forecast_line,
        )

    labels = _DETAIL_LABELS.get(language, _DETAIL_LABELS["en"])
    grid_label = locales.get_message(language, "online" if is_online else "offline")
    heading = labels["last_known"] if stale else labels["title"]
    lines = [f"📊 <b>{heading}</b>", f"{labels['grid']}: {grid_label}"]
    duration_line = locales.get_state_duration_line(language, is_online, state_duration)
    if duration_line:
        lines.append(duration_line.lstrip("\n"))
    voltage_line = locales.get_voltage_line(language, reading.grid_voltage) if is_online else ""
    if voltage_line:
        lines.append(voltage_line.lstrip("\n"))
    lines.append(locales.get_load_line(language, load_watts).lstrip("\n"))
    if forecast_action and forecast_text:
        safe_forecast = escape(str(forecast_text))
        forecast = locales.get_forecast_line(language, forecast_action, safe_forecast)
        if forecast:
            lines.append(forecast.lstrip("\n"))
    sections = [lines]

    lines = []
    battery_line = locales.get_battery_level_line(language, soc)
    if battery_line:
        lines.append(battery_line.lstrip("\n"))
    unit_a = "А" if language == "uk" else "A"
    unit_ah = "А·год" if language == "uk" else "Ah"
    unit_w = "Вт" if language == "uk" else "W"
    unit_kwh = "кВт·год" if language == "uk" else "kWh"
    discharge_power = getattr(reading, "battery_power_watts", None)
    if _finite(discharge_power) and float(discharge_power) > 0:
        lines.append(f"{labels['discharge']}: {float(discharge_power):.0f} {unit_w}")
    if charge_power_line:
        lines.append(charge_power_line.lstrip("\n"))
    if runtime_line:
        lines.extend(runtime_line.lstrip("\n").splitlines())
    if charge_time_line:
        lines.append(charge_time_line.lstrip("\n"))

    battery_voltage = getattr(reading, "battery_voltage", None)
    if _finite(battery_voltage) and float(battery_voltage) >= 0:
        unit_v = "В" if language == "uk" else "V"
        lines.append(f"{labels['battery_voltage']}: {float(battery_voltage):.1f} {unit_v}")
    current = getattr(reading, "battery_current", None)
    if _finite(current):
        lines.append(f"{labels['current']}: {abs(float(current)):.1f} {unit_a}")
    temperature_line = _temperature_line(
        labels["battery_temperature"], getattr(reading, "battery_temperature_c", None)
    )
    if temperature_line:
        lines.append(temperature_line)
    capacity = getattr(reading, "battery_capacity_ah", None)
    if _finite(capacity) and float(capacity) > 0:
        lines.append(f"{labels['capacity']}: {float(capacity):g} {unit_ah}")
    cutoff = reading.cutoff_soc(settings)
    if _finite(cutoff):
        lines.append(f"{labels['cutoff']}: {float(cutoff):g}%")
    sections.append(lines)

    lines = []
    for label, field in (
        ("inverter_dc_temperature", "inverter_dc_temperature_c"),
        ("inverter_radiator_temperature", "inverter_radiator_temperature_c"),
    ):
        temperature_line = _temperature_line(labels[label], getattr(reading, field, None))
        if temperature_line:
            lines.append(temperature_line)
    sections.append(lines)

    lines = []
    energy = getattr(reading, "total_load_energy_kwh", None)
    if _finite(energy) and float(energy) >= 0:
        lines.append(f"{labels['energy']}: {float(energy):.1f} {unit_kwh}")
    if observed_at is not None:
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        localized = observed_at.astimezone(settings.stats_timezone)
        lines.append(f"{labels['updated']}: {localized:%d.%m.%Y %H:%M:%S %Z}")
    sections.append(lines)
    return "\n\n".join("\n".join(section) for section in sections if section)
