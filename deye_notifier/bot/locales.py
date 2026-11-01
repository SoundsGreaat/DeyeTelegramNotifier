"""Localized Telegram messages and formatting helpers."""

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
        "warning_battery": "⚠️ <b>Warning: Battery Power</b>\nGrid voltage: {voltage}V\nLoad: {load}W\nOnline duration: {duration}{forecast_line}",
        "power_restored": "✅ <b>Power Restored</b>\nGrid voltage: {voltage}V\nOffline duration: {duration}{forecast_line}",
        "battery_full": "🔋 <b>Battery Fully Charged</b>\nLevel: 100%",
        "battery_charging": "📈 <b>Battery Charging</b>\nLevel: {level}%",
        "battery_discharging": "📉 <b>Battery Discharging</b>\nLevel: {level}%",
        "battery_low": "⚠️ <b>Battery Low</b>\nLevel: {level}%",
        "status": "📊 <b>System Status</b>\nGrid: {grid_status}\nVoltage: {voltage}V\nLoad: {load}W\nBattery: {battery}%{state_duration_line}{runtime_line}{forecast_line}",
        "online": "Online 🟢",
        "offline": "Offline 🔴",
        "connection_failed": "❌ Connection failed",
        "stats_failed": "❌ Could not build the electricity report.",
        "stats_usage": "Usage: /stats [day|week|month] (or /stats_day, /stats_week, /stats_month)",
        "stats_empty": "📭 Not enough data for this period yet.",
        "duration_unknown": "Duration: unknown",
    },
    "uk": {
        "start": "✅ Сповіщення про мережу та батарею ввімкнено.\n/status — стан; /stats day, /stats week або /stats month — звіти за запитом; /lang — мова.\nАвтозвіти потребують окремої підписки /stats_auto_on; /stats_auto_off — вимкнути.",
        "stop": "❌ Усі сповіщення та автоматичні звіти вимкнено.",
        "stats_auto_enabled": "✅ Автоматичні денні, тижневі й місячні звіти ввімкнено. /stats_auto_off — вимкнути звіти.",
        "stats_auto_disabled": "🔕 Автозвіти вимкнено.",
        "stats_auto_paused": "Автозвіти зараз призупинені в налаштуваннях бота. Вашу підписку збережено.",
        "select_lang": "Будь ласка, оберіть вашу мову:",
        "lang_set": "✅ Мову змінено на Українську.",
        "warning_battery": "⚠️ <b>Увага: Живлення від батареї</b>\nНапруга мережі: {voltage}В\nНавантаження: {load}Вт\nСвітло було: {duration}{forecast_line}",
        "power_restored": "✅ <b>Живлення відновлено</b>\nНапруга мережі: {voltage}В\nСвітла не було: {duration}{forecast_line}",
        "battery_full": "🔋 <b>Батарея повністю заряджена</b>\nРівень: 100%",
        "battery_charging": "📈 <b>Батарея заряджається</b>\nРівень: {level}%",
        "battery_discharging": "📉 <b>Батарея розряджається</b>\nРівень: {level}%",
        "battery_low": "⚠️ <b>Низький заряд батареї</b>\nРівень: {level}%",
        "status": "📊 <b>Статус системи</b>\nМережа: {grid_status}\nНапруга: {voltage}В\nНавантаження: {load}Вт\nБатарея: {battery}%{state_duration_line}{runtime_line}{forecast_line}",
        "online": "В мережі 🟢",
        "offline": "Відсутня 🔴",
        "connection_failed": "❌ Не вдалося підключитися до інвертора",
        "stats_failed": "❌ Не вдалося сформувати статистику.",
        "stats_usage": "Використання: /stats [day|week|month] (або /stats_day, /stats_week, /stats_month)",
        "stats_empty": "📭 Недостатньо даних за цей період.",
        "duration_unknown": "Тривалість: невідома",
    },
    "es": {
        "start": "✅ Avisos de red y batería activados.\n/status — estado; /stats day, /stats week o /stats month — informes a petición; /lang — idioma.\nLos informes automáticos requieren /stats_auto_on; /stats_auto_off los desactiva.",
        "stop": "❌ Todos los avisos e informes automáticos desactivados.",
        "stats_auto_enabled": "✅ Informes diarios, semanales y mensuales automáticos activados. /stats_auto_off — desactivarlos.",
        "stats_auto_disabled": "🔕 Informes automáticos desactivados.",
        "stats_auto_paused": "Los informes automáticos están pausados en los ajustes del bot. Tu suscripción se ha guardado.",
        "select_lang": "Por favor, selecciona tu idioma:",
        "lang_set": "✅ Idioma cambiado a Español.",
        "warning_battery": "⚠️ <b>Advertencia: Energía de Batería</b>\nVoltaje de red: {voltage}V\nCarga: {load}W\nDuración en línea: {duration}{forecast_line}",
        "power_restored": "✅ <b>Energía Restaurada</b>\nVoltaje de red: {voltage}V\nDuración fuera de línea: {duration}{forecast_line}",
        "battery_full": "🔋 <b>Batería Completamente Cargada</b>\nNivel: 100%",
        "battery_charging": "📈 <b>Batería cargándose</b>\nNivel: {level}%",
        "battery_discharging": "📉 <b>Batería descargándose</b>\nNivel: {level}%",
        "battery_low": "⚠️ <b>Batería baja</b>\nNivel: {level}%",
        "status": "📊 <b>Estado del Sistema</b>\nRed: {grid_status}\nVoltaje: {voltage}V\nCarga: {load}W\nBatería: {battery}%{state_duration_line}{runtime_line}{forecast_line}",
        "online": "En línea 🟢",
        "offline": "Fuera de línea 🔴",
        "connection_failed": "❌ No se pudo conectar",
        "stats_failed": "❌ No se pudo crear el informe eléctrico.",
        "stats_usage": "Uso: /stats [day|week|month] (o /stats_day, /stats_week, /stats_month)",
        "stats_empty": "📭 Aún no hay suficientes datos para este período.",
        "duration_unknown": "Duración: desconocida",
    },
    "de": {
        "start": "✅ Netz- und Batteriemeldungen aktiviert.\n/status — Zustand; /stats day, /stats week oder /stats month — Berichte auf Anfrage; /lang — Sprache.\nAutomatische Berichte separat mit /stats_auto_on aktivieren; /stats_auto_off — deaktivieren.",
        "stop": "❌ Alle Meldungen und automatischen Berichte deaktiviert.",
        "stats_auto_enabled": "✅ Automatische Tages-, Wochen- und Monatsberichte aktiviert. /stats_auto_off — deaktivieren.",
        "stats_auto_disabled": "🔕 Automatische Berichte deaktiviert.",
        "stats_auto_paused": "Automatische Berichte sind in den Bot-Einstellungen pausiert. Ihr Abonnement ist gespeichert.",
        "select_lang": "Bitte wählen Sie Ihre Sprache:",
        "lang_set": "✅ Sprache auf Deutsch eingestellt.",
        "warning_battery": "⚠️ <b>Warnung: Batteriebetrieb</b>\nNetzspannung: {voltage}V\nLast: {load}W\nOnline-Dauer: {duration}{forecast_line}",
        "power_restored": "✅ <b>Strom wiederhergestellt</b>\nNetzspannung: {voltage}V\nOffline-Dauer: {duration}{forecast_line}",
        "battery_full": "🔋 <b>Batterie voll geladen</b>\nStand: 100%",
        "battery_charging": "📈 <b>Batterie wird geladen</b>\nStand: {level}%",
        "battery_discharging": "📉 <b>Batterie entlädt</b>\nStand: {level}%",
        "battery_low": "⚠️ <b>Batteriestand niedrig</b>\nStand: {level}%",
        "status": "📊 <b>Systemstatus</b>\nNetz: {grid_status}\nSpannung: {voltage}V\nLast: {load}W\nBatterie: {battery}%{state_duration_line}{runtime_line}{forecast_line}",
        "online": "Online 🟢",
        "offline": "Offline 🔴",
        "connection_failed": "❌ Verbindung fehlgeschlagen",
        "stats_failed": "❌ Strombericht konnte nicht erstellt werden.",
        "stats_usage": "Verwendung: /stats [day|week|month] (oder /stats_day, /stats_week, /stats_month)",
        "stats_empty": "📭 Noch nicht genug Daten für diesen Zeitraum.",
        "duration_unknown": "Dauer: unbekannt",
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
            "forecast_line": "",
            **kwargs,
        }
        return msg.format(**values)
    return msg


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


def get_state_duration_line(lang_code, is_grid_online, seconds):
    if seconds is None:
        return f"\n{get_message(lang_code, 'duration_unknown')}"
    labels = {
        "en": ("\n💡 Grid available: {duration}", "\n🕯 Grid unavailable: {duration}"),
        "uk": ("\n💡 Світло є вже: {duration}", "\n🕯 Світла немає вже: {duration}"),
        "es": ("\n💡 Red disponible: {duration}", "\n🕯 Red ausente: {duration}"),
        "de": ("\n💡 Netz verfügbar seit: {duration}", "\n🕯 Netz fehlt seit: {duration}"),
    }
    return (labels.get(lang_code, labels["en"])[0 if is_grid_online else 1]).format(
        duration=format_duration(seconds, lang_code)
    )


def get_runtime_line(lang_code, battery_soc, load_watts, capacity_kwh, cutoff_soc, efficiency=1.0):
    text = {
        "en": (
            "\n⏱ Estimated runtime to {cutoff}%: {duration}",
            "Usable reserve: {energy:.2f} kWh",
            "\n⏱ Runtime estimate: set BATTERY_CAPACITY_KWH",
            "\n⏱ Runtime estimate: unavailable (load is zero)",
        ),
        "uk": (
            "\n⏱ Орієнтовний час до {cutoff}%: {duration}",
            "Доступний запас: {energy:.2f} кВт·год",
            "\n⏱ Оцінка часу: вкажіть BATTERY_CAPACITY_KWH",
            "\n⏱ Оцінка часу: недоступна (навантаження нульове)",
        ),
        "es": (
            "\n⏱ Autonomía estimada hasta {cutoff}%: {duration}",
            "Reserva utilizable: {energy:.2f} kWh",
            "\n⏱ Autonomía: configure BATTERY_CAPACITY_KWH",
            "\n⏱ Autonomía: no disponible (carga cero)",
        ),
        "de": (
            "\n⏱ Geschätzte Laufzeit bis {cutoff}%: {duration}",
            "Nutzbare Reserve: {energy:.2f} kWh",
            "\n⏱ Laufzeit: BATTERY_CAPACITY_KWH konfigurieren",
            "\n⏱ Laufzeit: nicht verfügbar (keine Last)",
        ),
    }
    head, energy_label, unconfigured, zero = text.get(lang_code, text["en"])
    try:
        soc = float(battery_soc)
        load = float(load_watts)
        capacity = float(capacity_kwh)
        cutoff = float(cutoff_soc)
        eta = max(0.0, float(efficiency))
    except (TypeError, ValueError):
        return unconfigured
    if capacity <= 0:
        return unconfigured
    if load <= 0:
        return zero
    usable = max(0.0, soc - cutoff) / 100.0 * capacity * eta
    cutoff_text = int(cutoff) if cutoff.is_integer() else cutoff
    duration = format_duration(usable / (load / 1000.0) * 3600, lang_code)
    return head.format(cutoff=cutoff_text, duration=duration) + "\n" + energy_label.format(energy=usable)


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
    return (
        f"📈 <b>{title}</b>\n🗓 {interval}\n\n"
        f"⚡ <b>{labels['consumption']}:</b> {float(stats.get('energy_kwh', 0) or 0):.2f} {labels['energy_unit']}\n"
        f"📊 {labels['avg']}: {avg_text} · {labels['peak']}: {peak_text}\n"
        f"🟢 {labels['online']}: {_hours(online_seconds)} {labels['time_unit']} ({online_share}%)\n"
        f"🔴 {labels['offline']}: {_hours(offline_seconds)} {labels['time_unit']} ({offline_share}%)\n"
        f"⚪ {labels['unknown']}: {_hours(unknown_seconds)} {labels['time_unit']} ({unknown_share}%)\n"
        f"🔌 {labels['outages']}: {int(stats.get('outages', 0) or 0)} · {labels['longest']}: {longest}\n"
        f"🔋 {labels['soc']}: {soc}\n📡 {labels['coverage']}: {float(stats.get('coverage_percent', 0) or 0):.0f}%"
    )
