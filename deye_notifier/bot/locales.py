"""Localized Telegram messages and formatting helpers."""

import math
import re
from datetime import timezone

MESSAGES = {
    "en": {
        "start": "✅ Power and battery notifications enabled.\n/status — current state; /stats day, /stats week or /stats month — reports on demand; /lang — language.\nAutomatic reports require a separate /stats_auto_on subscription; /stats_auto_off disables them.",
        "stop": "❌ All notifications and automatic reports disabled.",
        "stats_auto_enabled": "✅ Automatic daily, weekly and monthly reports enabled. /stats_auto_off — disable reports.",
        "stats_auto_disabled": "🔕 Automatic reports disabled.",
        "stats_auto_paused": "Automatic reports are currently paused in the bot settings. Your subscription is saved.",
        "select_lang": "Please select your language:",
        "lang_set": "✅ Language set to English.",
        "warning_battery": "⚠️ <b>Power Outage</b>\nGrid was available: {duration}{load_line}{battery_level_line}{runtime_line}{forecast_line}",
        "outage_load": "⚡ Load: {load:.0f} W{runtime_line}",
        "power_restored": "✅ <b>Power Restored</b>\nGrid was unavailable: {duration}{voltage_line}{forecast_line}",
        "grid_voltage_high": "⚠️ <b>High grid voltage</b>{voltage_line}",
        "grid_voltage_low": "⚠️ <b>Low grid voltage</b>{voltage_line}",
        "grid_voltage_normal": "✅ <b>Grid voltage recovered</b>{voltage_line}",
        "grid_disconnected": "⚠️ <b>Inverter disconnected from grid</b>{voltage_line}{load_line}{battery_level_line}{runtime_line}",
        "grid_reconnected": "✅ <b>Inverter reconnected to grid</b>{voltage_line}",
        "inverter_disconnected": "⚠️ Inverter disconnected from grid",
        "voltage_high": "⚠️ High grid voltage",
        "voltage_low": "⚠️ Low grid voltage",
        "battery_full": "🔋 <b>Battery Fully Charged</b>\nLevel: 100%",
        "battery_charging": "📈 <b>Battery Charging</b>\nLevel: {level}%{charge_power_line}{charge_time_line}",
        "battery_discharging": "📉 <b>Battery Discharging</b>\nLevel: {level}%{load_line}{runtime_line}",
        "battery_low": "⚠️ <b>Battery Low</b>\nLevel: {level}%{load_line}{runtime_line}",
        "status": "📊 <b>System Status</b>\nGrid: {grid_status}{state_duration_line}{voltage_line}{grid_warning_line}{grid_connection_line}\nLoad: {load:.0f} W\nBattery: {battery}%{runtime_line}{charge_power_line}{charge_time_line}{forecast_line}",
        "online": "Online 🟢",
        "offline": "Offline 🔴",
        "connection_failed": "❌ Connection failed",
        "stats_failed": "❌ Could not build the electricity report.",
        "stats_usage": "Usage: /stats [day|week|month] (or /stats_day, /stats_week, /stats_month)",
        "stats_empty": "📭 Not enough data for this period yet.",
        "live_stopped": "⏸ Live status updates stopped.",
        "live_replaced": "⏸ This status is inactive; a newer live message has replaced it.",
        "live_pin_failed": "Live status is updating. If the message is not pinned, you can pin it manually.",
        "live_failed": "❌ Could not update live status. Please try again.",
    },
    "uk": {
        "start": "✅ Сповіщення про мережу та батарею ввімкнено.\n/status — стан; /stats day, /stats week або /stats month — звіти за запитом; /lang — мова.\nАвтозвіти потребують окремої підписки /stats_auto_on; /stats_auto_off — вимкнути.",
        "stop": "❌ Усі сповіщення та автоматичні звіти вимкнено.",
        "stats_auto_enabled": "✅ Автоматичні денні, тижневі й місячні звіти ввімкнено. /stats_auto_off — вимкнути звіти.",
        "stats_auto_disabled": "🔕 Автозвіти вимкнено.",
        "stats_auto_paused": "Автозвіти зараз призупинені в налаштуваннях бота. Вашу підписку збережено.",
        "select_lang": "Будь ласка, оберіть вашу мову:",
        "lang_set": "✅ Мову змінено на Українську.",
        "warning_battery": "⚠️ <b>Світло вимкнено</b>\nСвітло було: {duration}{load_line}{battery_level_line}{runtime_line}{forecast_line}",
        "outage_load": "⚡ Навантаження: {load:.0f} Вт{runtime_line}",
        "power_restored": "✅ <b>Світло увімкнено</b>\nСвітла не було: {duration}{voltage_line}{forecast_line}",
        "grid_voltage_high": "⚠️ <b>Висока напруга мережі</b>{voltage_line}",
        "grid_voltage_low": "⚠️ <b>Низька напруга мережі</b>{voltage_line}",
        "grid_voltage_normal": "✅ <b>Напруга мережі відновилася</b>{voltage_line}",
        "grid_disconnected": "⚠️ <b>Інвертор відключився від мережі</b>{voltage_line}{load_line}{battery_level_line}{runtime_line}",
        "grid_reconnected": "✅ <b>Інвертор підключився до мережі</b>{voltage_line}",
        "inverter_disconnected": "⚠️ Інвертор відключений від мережі",
        "voltage_high": "⚠️ Висока напруга мережі",
        "voltage_low": "⚠️ Низька напруга мережі",
        "battery_full": "🔋 <b>Батарея повністю заряджена</b>\nРівень: 100%",
        "battery_charging": "📈 <b>Батарея заряджається</b>\nРівень: {level}%{charge_power_line}{charge_time_line}",
        "battery_discharging": "📉 <b>Батарея розряджається</b>\nРівень: {level}%{load_line}{runtime_line}",
        "battery_low": "⚠️ <b>Низький заряд батареї</b>\nРівень: {level}%{load_line}{runtime_line}",
        "status": "📊 <b>Статус системи</b>\nМережа: {grid_status}{state_duration_line}{voltage_line}{grid_warning_line}{grid_connection_line}\nНавантаження: {load:.0f} Вт\nБатарея: {battery}%{runtime_line}{charge_power_line}{charge_time_line}{forecast_line}",
        "online": "В мережі 🟢",
        "offline": "Відсутня 🔴",
        "connection_failed": "❌ Не вдалося підключитися до інвертора",
        "stats_failed": "❌ Не вдалося сформувати статистику.",
        "stats_usage": "Використання: /stats [day|week|month] (або /stats_day, /stats_week, /stats_month)",
        "stats_empty": "📭 Недостатньо даних за цей період.",
        "live_stopped": "⏸ Оновлення статусу зупинено.",
        "live_replaced": "⏸ Цей статус неактивний: створено нове повідомлення.",
        "live_pin_failed": "Статус оновлюється. Якщо повідомлення не закріплене, закріпіть його вручну.",
        "live_failed": "❌ Не вдалося оновити живий статус. Спробуйте ще раз.",
    },
    "es": {
        "start": "✅ Avisos de red y batería activados.\n/status — estado; /stats day, /stats week o /stats month — informes a petición; /lang — idioma.\nLos informes automáticos requieren /stats_auto_on; /stats_auto_off los desactiva.",
        "stop": "❌ Todos los avisos e informes automáticos desactivados.",
        "stats_auto_enabled": "✅ Informes diarios, semanales y mensuales automáticos activados. /stats_auto_off — desactivarlos.",
        "stats_auto_disabled": "🔕 Informes automáticos desactivados.",
        "stats_auto_paused": "Los informes automáticos están pausados en los ajustes del bot. Tu suscripción se ha guardado.",
        "select_lang": "Por favor, selecciona tu idioma:",
        "lang_set": "✅ Idioma cambiado a Español.",
        "warning_battery": "⚠️ <b>Corte de electricidad</b>\nLa red estuvo disponible: {duration}{load_line}{battery_level_line}{runtime_line}{forecast_line}",
        "outage_load": "⚡ Carga: {load:.0f} W{runtime_line}",
        "power_restored": "✅ <b>Energía restaurada</b>\nLa red estuvo ausente: {duration}{voltage_line}{forecast_line}",
        "grid_voltage_high": "⚠️ <b>Voltaje de red alto</b>{voltage_line}",
        "grid_voltage_low": "⚠️ <b>Voltaje de red bajo</b>{voltage_line}",
        "grid_voltage_normal": "✅ <b>Voltaje de red recuperado</b>{voltage_line}",
        "grid_disconnected": "⚠️ <b>Inversor desconectado de la red</b>{voltage_line}{load_line}{battery_level_line}{runtime_line}",
        "grid_reconnected": "✅ <b>Inversor reconectado a la red</b>{voltage_line}",
        "inverter_disconnected": "⚠️ Inversor desconectado de la red",
        "voltage_high": "⚠️ Voltaje de red alto",
        "voltage_low": "⚠️ Voltaje de red bajo",
        "battery_full": "🔋 <b>Batería Completamente Cargada</b>\nNivel: 100%",
        "battery_charging": "📈 <b>Batería cargándose</b>\nNivel: {level}%{charge_power_line}{charge_time_line}",
        "battery_discharging": "📉 <b>Batería descargándose</b>\nNivel: {level}%{load_line}{runtime_line}",
        "battery_low": "⚠️ <b>Batería baja</b>\nNivel: {level}%{load_line}{runtime_line}",
        "status": "📊 <b>Estado del sistema</b>\nRed: {grid_status}{state_duration_line}{voltage_line}{grid_warning_line}{grid_connection_line}\nCarga: {load:.0f} W\nBatería: {battery}%{runtime_line}{charge_power_line}{charge_time_line}{forecast_line}",
        "online": "En línea 🟢",
        "offline": "Fuera de línea 🔴",
        "connection_failed": "❌ No se pudo conectar",
        "stats_failed": "❌ No se pudo crear el informe eléctrico.",
        "stats_usage": "Uso: /stats [day|week|month] (o /stats_day, /stats_week, /stats_month)",
        "stats_empty": "📭 Aún no hay suficientes datos para este período.",
        "live_stopped": "⏸ Actualizaciones del estado detenidas.",
        "live_replaced": "⏸ Este estado está inactivo; hay un mensaje más reciente.",
        "live_pin_failed": "El estado se actualiza. Si el mensaje no está fijado, puedes fijarlo manualmente.",
        "live_failed": "❌ No se pudo actualizar el estado. Inténtalo de nuevo.",
    },
    "de": {
        "start": "✅ Netz- und Batteriemeldungen aktiviert.\n/status — Zustand; /stats day, /stats week oder /stats month — Berichte auf Anfrage; /lang — Sprache.\nAutomatische Berichte separat mit /stats_auto_on aktivieren; /stats_auto_off — deaktivieren.",
        "stop": "❌ Alle Meldungen und automatischen Berichte deaktiviert.",
        "stats_auto_enabled": "✅ Automatische Tages-, Wochen- und Monatsberichte aktiviert. /stats_auto_off — deaktivieren.",
        "stats_auto_disabled": "🔕 Automatische Berichte deaktiviert.",
        "stats_auto_paused": "Automatische Berichte sind in den Bot-Einstellungen pausiert. Ihr Abonnement ist gespeichert.",
        "select_lang": "Bitte wählen Sie Ihre Sprache:",
        "lang_set": "✅ Sprache auf Deutsch eingestellt.",
        "warning_battery": "⚠️ <b>Stromausfall</b>\nNetz war verfügbar: {duration}{load_line}{battery_level_line}{runtime_line}{forecast_line}",
        "outage_load": "⚡ Last: {load:.0f} W{runtime_line}",
        "power_restored": "✅ <b>Strom wiederhergestellt</b>\nNetz war ausgefallen: {duration}{voltage_line}{forecast_line}",
        "grid_voltage_high": "⚠️ <b>Hohe Netzspannung</b>{voltage_line}",
        "grid_voltage_low": "⚠️ <b>Niedrige Netzspannung</b>{voltage_line}",
        "grid_voltage_normal": "✅ <b>Netzspannung wieder im Bereich</b>{voltage_line}",
        "grid_disconnected": "⚠️ <b>Wechselrichter vom Netz getrennt</b>{voltage_line}{load_line}{battery_level_line}{runtime_line}",
        "grid_reconnected": "✅ <b>Wechselrichter wieder mit dem Netz verbunden</b>{voltage_line}",
        "inverter_disconnected": "⚠️ Wechselrichter vom Netz getrennt",
        "voltage_high": "⚠️ Hohe Netzspannung",
        "voltage_low": "⚠️ Niedrige Netzspannung",
        "battery_full": "🔋 <b>Batterie voll geladen</b>\nStand: 100%",
        "battery_charging": "📈 <b>Batterie wird geladen</b>\nStand: {level}%{charge_power_line}{charge_time_line}",
        "battery_discharging": "📉 <b>Batterie entlädt</b>\nStand: {level}%{load_line}{runtime_line}",
        "battery_low": "⚠️ <b>Batteriestand niedrig</b>\nStand: {level}%{load_line}{runtime_line}",
        "status": "📊 <b>Systemstatus</b>\nNetz: {grid_status}{state_duration_line}{voltage_line}{grid_warning_line}{grid_connection_line}\nLast: {load:.0f} W\nBatterie: {battery}%{runtime_line}{charge_power_line}{charge_time_line}{forecast_line}",
        "online": "Online 🟢",
        "offline": "Offline 🔴",
        "connection_failed": "❌ Verbindung fehlgeschlagen",
        "stats_failed": "❌ Strombericht konnte nicht erstellt werden.",
        "stats_usage": "Verwendung: /stats [day|week|month] (oder /stats_day, /stats_week, /stats_month)",
        "stats_empty": "📭 Noch nicht genug Daten für diesen Zeitraum.",
        "live_stopped": "⏸ Statusaktualisierungen gestoppt.",
        "live_replaced": "⏸ Dieser Status ist inaktiv; eine neuere Nachricht hat ihn ersetzt.",
        "live_pin_failed": "Der Status wird aktualisiert. Falls die Nachricht nicht angeheftet ist, können Sie sie manuell anheften.",
        "live_failed": "❌ Status konnte nicht aktualisiert werden. Bitte erneut versuchen.",
    },
}

LANGUAGES = {
    "en": "English",
    "uk": "Українська",
    "es": "Español",
    "de": "Deutsch",
}


def normalize_language(lang_code):
    return lang_code if lang_code in LANGUAGES else "en"


def get_message(lang_code, key, **kwargs):
    lang = MESSAGES.get(lang_code, MESSAGES["en"])
    msg = lang.get(key, MESSAGES["en"].get(key, key))
    if kwargs or "{" in msg:
        values = {
            "state_duration_line": "",
            "runtime_line": "",
            "charge_time_line": "",
            "charge_power_line": "",
            "forecast_line": "",
            "load_line": "",
            "battery_level_line": "",
            "voltage_line": "",
            "grid_warning_line": "",
            "grid_connection_line": "",
            **kwargs,
        }
        return msg.format(**values)
    return msg


def get_grid_connection_line(lang_code, is_online, connected):
    if is_online and connected is False:
        return "\n" + get_message(lang_code, "inverter_disconnected")
    return ""


def get_grid_warning_line(lang_code, is_online, voltage, minimum, maximum):
    if not is_online:
        return ""
    key = "voltage_high" if voltage > maximum else "voltage_low" if voltage < minimum else None
    return "\n" + get_message(lang_code, key) if key else ""


def get_forecast_line(lang_code, action, forecast):
    if not action or not forecast:
        return ""
    labels = {
        "en": {"turn_on": "Expected power restoration", "turn_off": "Expected power outage"},
        "uk": {"turn_on": "Очікуване включення світла", "turn_off": "Очікуване вимкнення світла"},
        "es": {"turn_on": "Próxima restauración prevista", "turn_off": "Próximo apagón previsto"},
        "de": {"turn_on": "Voraussichtliche Wiedereinschaltung", "turn_off": "Voraussichtliche Abschaltung"},
    }
    label = labels.get(lang_code, labels["en"]).get(action, labels["en"]["turn_on"])
    return f"\n📅 {label}: {forecast}"


_DURATION_UNITS = {
    "en": ("d", "h", "m", "s"),
    "uk": ("д", "г", "хв", "с"),
    "es": ("d", "h", "min", "s"),
    "de": ("T", "Std", "Min", "Sek"),
}


def format_duration(seconds, lang_code="en"):
    if seconds is None:
        return "—"
    try:
        total = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "—"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    units = _DURATION_UNITS.get(lang_code, _DURATION_UNITS["en"])
    parts = [
        f"{n}{u}"
        for n, u in ((days, units[0]), (hours, units[1]), (minutes, units[2]), (secs, units[3]))
        if n
    ]
    return " ".join(parts) if parts else f"0{units[3]}"


def format_saved_duration(value, lang_code):
    if not isinstance(value, str) or not re.fullmatch(r"(?:\d+[dhms]\s*)+", value):
        return value
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    seconds = sum(int(number) * units[unit] for number, unit in re.findall(r"(\d+)([dhms])", value))
    return format_duration(seconds, lang_code)


def get_voltage_line(lang_code, volts):
    if volts is None or not math.isfinite(volts) or volts < 0:
        return ""
    labels = {"en": "Voltage", "uk": "Напруга", "es": "Voltaje", "de": "Spannung"}
    unit = "В" if lang_code == "uk" else "V"
    return f"\n{labels.get(lang_code, labels['en'])}: {volts:.1f} {unit}"


def get_load_line(lang_code, watts):
    if watts is None or not math.isfinite(watts) or watts < 0:
        return ""
    labels = {"en": "Load", "uk": "Навантаження", "es": "Carga", "de": "Last"}
    unit = "Вт" if lang_code == "uk" else "W"
    return f"\n{labels.get(lang_code, labels['en'])}: {watts:.0f} {unit}"


def get_battery_level_line(lang_code, level):
    if level is None or not math.isfinite(level) or not 0 <= level <= 100:
        return ""
    labels = {"en": "Battery", "uk": "Батарея", "es": "Batería", "de": "Batterie"}
    return f"\n{labels.get(lang_code, labels['en'])}: {level:g}%"


def _estimate_duration(seconds, lang_code):
    rounded = max(60, round(seconds / 60) * 60) if seconds > 0 else 0
    return format_duration(rounded, lang_code)


def get_state_duration_line(lang_code, is_grid_online, seconds):
    if seconds is None:
        return ""
    labels = {
        "en": ("\n💡 Grid available: {duration}", "\n🕯 Grid unavailable: {duration}"),
        "uk": ("\n💡 Світло є вже: {duration}", "\n🕯 Світла немає вже: {duration}"),
        "es": ("\n💡 Red disponible: {duration}", "\n🕯 Red ausente: {duration}"),
        "de": ("\n💡 Netz verfügbar seit: {duration}", "\n🕯 Netz fehlt seit: {duration}"),
    }
    return (labels.get(lang_code, labels["en"])[0 if is_grid_online else 1]).format(
        duration=format_duration(seconds, lang_code)
    )


def get_runtime_line(
    lang_code,
    battery_soc,
    load_watts,
    capacity_kwh,
    cutoff_soc,
    efficiency=1.0,
    *,
    include_energy=True,
    runtime_seconds=None,
):
    text = {
        "en": (
            "\n⏱ Estimated runtime to {cutoff}%: {duration}",
            "Usable reserve: {energy:.2f} kWh",
        ),
        "uk": (
            "\n⏱ Орієнтовний час до {cutoff}%: {duration}",
            "Доступний запас: {energy:.2f} кВт·год",
        ),
        "es": (
            "\n⏱ Autonomía estimada hasta {cutoff}%: {duration}",
            "Reserva utilizable: {energy:.2f} kWh",
        ),
        "de": (
            "\n⏱ Geschätzte Laufzeit bis {cutoff}%: {duration}",
            "Nutzbare Reserve: {energy:.2f} kWh",
        ),
    }
    head, energy_label = text.get(lang_code, text["en"])
    try:
        soc = float(battery_soc)
        load = float(load_watts)
        capacity = float(capacity_kwh)
        cutoff = float(cutoff_soc)
        eta = max(0.0, float(efficiency))
    except (TypeError, ValueError):
        return ""
    if runtime_seconds is not None:
        cutoff_text = int(cutoff) if cutoff.is_integer() else cutoff
        line = head.format(cutoff=cutoff_text, duration=_estimate_duration(runtime_seconds, lang_code))
        if not include_energy or load <= 0:
            return line
        # Estimate the deliverable AC reserve at the measured DC discharge
        # rate and current AC load, rather than assuming nominal voltage or conversion efficiency.
        usable = runtime_seconds * load / 3_600_000
        return line + "\n" + energy_label.format(energy=usable)
    if capacity <= 0:
        return ""
    if load <= 0:
        return ""
    usable = max(0.0, soc - cutoff) / 100.0 * capacity * eta
    cutoff_text = int(cutoff) if cutoff.is_integer() else cutoff
    duration = _estimate_duration(usable / (load / 1000.0) * 3600, lang_code)
    line = head.format(cutoff=cutoff_text, duration=duration)
    return line + "\n" + energy_label.format(energy=usable) if include_energy else line


def get_charge_power_line(lang_code, watts):
    if watts is None or not math.isfinite(watts) or watts <= 0:
        return ""
    labels = {
        "en": "\n⚡ Charging: {watts:.0f} W",
        "uk": "\n⚡ Заряджання: {watts:.0f} Вт",
        "es": "\n⚡ Potencia de carga: {watts:.0f} W",
        "de": "\n⚡ Ladeleistung: {watts:.0f} W",
    }
    return labels.get(lang_code, labels["en"]).format(watts=watts)


def get_charge_time_line(lang_code, seconds_remaining):
    labels = {
        "en": "\n⏱ To 100%: about {duration}",
        "uk": "\n⏱ До 100%: приблизно {duration}",
        "es": "\n⏱ Hasta el 100%: aprox. {duration}",
        "de": "\n⏱ Bis 100%: ca. {duration}",
    }
    estimate = labels.get(lang_code, labels["en"])
    if seconds_remaining is None or not math.isfinite(seconds_remaining) or seconds_remaining < 0:
        return ""
    return estimate.format(duration=_estimate_duration(seconds_remaining, lang_code))


def _hours(seconds):
    """Return hours without a unit; the caller supplies the locale's unit."""
    return "—" if seconds is None else f"{max(0.0, float(seconds)) / 3600.0:.1f}"


_REPORT_LABELS = {
    "en": {
        "titles": {
            "day": "Daily electricity statistics",
            "week": "Weekly electricity statistics",
            "month": "Monthly electricity statistics",
        },
        "consumption": "Estimated consumption",
        "avg": "Average load",
        "peak": "Peak load",
        "online": "Grid available",
        "offline": "Grid unavailable",
        "outages": "Observed outages",
        "longest": "Longest outage",
        "soc": "Battery range",
        "coverage": "Coverage",
        "unknown": "Unknown gaps",
        "energy_unit": "kWh",
        "power_unit": "W",
        "time_unit": "h",
    },
    "uk": {
        "titles": {
            "day": "Денна статистика електроенергії",
            "week": "Тижнева статистика електроенергії",
            "month": "Місячна статистика електроенергії",
        },
        "consumption": "Орієнтовне споживання",
        "avg": "Середнє навантаження",
        "peak": "Пікове навантаження",
        "online": "Світло є",
        "offline": "Світла немає",
        "outages": "Зафіксовані відключення",
        "longest": "Найдовше відключення",
        "soc": "Діапазон заряду",
        "coverage": "Покриття даними",
        "unknown": "Невідомі проміжки",
        "energy_unit": "кВт·год",
        "power_unit": "Вт",
        "time_unit": "г",
    },
    "es": {
        "titles": {
            "day": "Estadística eléctrica diaria",
            "week": "Estadística eléctrica semanal",
            "month": "Estadística eléctrica mensual",
        },
        "consumption": "Consumo estimado",
        "avg": "Carga media",
        "peak": "Carga máxima",
        "online": "Red disponible",
        "offline": "Red ausente",
        "outages": "Cortes observados",
        "longest": "Corte más largo",
        "soc": "Rango de batería",
        "coverage": "Cobertura",
        "unknown": "Intervalos desconocidos",
        "energy_unit": "kWh",
        "power_unit": "W",
        "time_unit": "h",
    },
    "de": {
        "titles": {
            "day": "Tägliche Stromstatistik",
            "week": "Wöchentliche Stromstatistik",
            "month": "Monatliche Stromstatistik",
        },
        "consumption": "Geschätzter Verbrauch",
        "avg": "Durchschnittslast",
        "peak": "Spitzenlast",
        "online": "Netz verfügbar",
        "offline": "Netz fehlt",
        "outages": "Beobachtete Ausfälle",
        "longest": "Längster Ausfall",
        "soc": "Batteriebereich",
        "coverage": "Datenabdeckung",
        "unknown": "Unbekannte Lücken",
        "energy_unit": "kWh",
        "power_unit": "W",
        "time_unit": "Std",
    },
}


def format_statistics(lang_code, period, start, end, stats):
    if not stats or float(stats.get("coverage_percent", 0) or 0) <= 0:
        return get_message(lang_code, "stats_empty")

    labels = _REPORT_LABELS.get(lang_code, _REPORT_LABELS["en"])
    title = labels["titles"].get(period, period.title())
    total_seconds = max(0.0, (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds())
    online_seconds = max(0.0, float(stats.get("online_seconds", 0) or 0))
    offline_seconds = max(0.0, float(stats.get("offline_seconds", 0) or 0))
    reported_unknown = max(0.0, float(stats.get("unknown_seconds", 0) or 0))
    unknown_seconds = max(reported_unknown, total_seconds - online_seconds - offline_seconds)
    denominator = max(
        total_seconds,
        online_seconds + offline_seconds + unknown_seconds,
        1.0,
    )

    def share(seconds):
        return min(100.0, max(0.0, seconds / denominator * 100.0))

    timezone_name = getattr(start.tzinfo, "key", None) or start.strftime("%Z %z").strip() or "local time"
    interval = f"{start:%d.%m.%Y %H:%M} — {end:%d.%m.%Y %H:%M} ({timezone_name})"
    avg, peak = stats.get("avg_load_watts"), stats.get("peak_load_watts")
    min_soc, max_soc = stats.get("min_soc"), stats.get("max_soc")
    soc = f"{min_soc}–{max_soc}%" if min_soc is not None and max_soc is not None else "—"
    longest = format_duration(stats.get("longest_outage_seconds", 0), lang_code)
    online_share = min(100, round(share(online_seconds)))
    offline_share = min(100 - online_share, round(share(offline_seconds)))
    unknown_share = max(0, 100 - online_share - offline_share)
    avg_text = f"{float(avg):.0f} {labels['power_unit']}" if avg is not None else "—"
    peak_text = f"{int(peak)} {labels['power_unit']}" if peak is not None else "—"
    unknown_line = (
        f"⚪ {labels['unknown']}: {_hours(unknown_seconds)} {labels['time_unit']} ({unknown_share}%)\n"
        if unknown_seconds > 0
        else ""
    )
    outages = int(stats.get("outages", 0) or 0)
    outage_line = f"🔌 {labels['outages']}: {outages}"
    if float(stats.get("longest_outage_seconds", 0) or 0) > 0:
        outage_line += f" · {labels['longest']}: {longest}"
    return (
        f"📈 <b>{title}</b>\n🗓 {interval}\n\n"
        f"⚡ <b>{labels['consumption']}:</b> {float(stats.get('energy_kwh', 0) or 0):.2f} {labels['energy_unit']}\n"
        f"📊 {labels['avg']}: {avg_text} · {labels['peak']}: {peak_text}\n"
        f"🟢 {labels['online']}: {_hours(online_seconds)} {labels['time_unit']} ({online_share}%)\n"
        f"🔴 {labels['offline']}: {_hours(offline_seconds)} {labels['time_unit']} ({offline_share}%)\n"
        f"{unknown_line}{outage_line}\n"
        f"🔋 {labels['soc']}: {soc}\n📡 {labels['coverage']}: {float(stats.get('coverage_percent', 0) or 0):.0f}%"
    )
