MESSAGES = {
    "en": {
        "start": "✅ You have subscribed to inverter status notifications.",
        "stop": "❌ You have unsubscribed from notifications.",
        "select_lang": "Please select your language:",
        "lang_set": "✅ Language set to English.",
        "warning_battery": "⚠️ <b>Warning: Battery Power</b>\nGrid voltage: {voltage}V",
        "power_restored": "✅ <b>Power Restored</b>\nGrid voltage: {voltage}V",
        "battery_full": "🔋 <b>Battery Fully Charged</b>\nLevel: 100%",
        "battery_discharging": "📉 <b>Battery Discharging</b>\nLevel: {level}%",
        "battery_low": "⚠️ <b>Battery Low</b>\nLevel: {level}%",
        "status": "📊 <b>System Status</b>\nGrid: {grid_status}\nVoltage: {voltage}V\nBattery: {battery}%",
        "online": "Online 🟢",
        "offline": "Offline 🔴",
    },
    "uk": {
        "start": "✅ Ви підписалися на сповіщення про статус інвертора.",
        "stop": "❌ Ви відписалися від сповіщень.",
        "select_lang": "Будь ласка, оберіть вашу мову:",
        "lang_set": "✅ Мову змінено на Українську.",
        "warning_battery": "⚠️ <b>Увага: Живлення від батареї</b>\nНапруга мережі: {voltage}В",
        "power_restored": "✅ <b>Живлення відновлено</b>\nНапруга мережі: {voltage}В",
        "battery_full": "🔋 <b>Батарея повністю заряджена</b>\nРівень: 100%",
        "battery_discharging": "📉 <b>Батарея розряджається</b>\nРівень: {level}%",
        "battery_low": "⚠️ <b>Низький заряд батареї</b>\nРівень: {level}%",
        "status": "📊 <b>Статус системи</b>\nМережа: {grid_status}\nНапруга: {voltage}В\nБатарея: {battery}%",
        "online": "В мережі 🟢",
        "offline": "Відсутня 🔴",
    },
    "es": {
        "start": "✅ Te has suscrito a las notificaciones del estado del inversor.",
        "stop": "❌ Te has dado de baja de las notificaciones.",
        "select_lang": "Por favor, selecciona tu idioma:",
        "lang_set": "✅ Idioma cambiado a Español.",
        "warning_battery": "⚠️ <b>Advertencia: Energía de Batería</b>\nVoltaje de red: {voltage}V",
        "power_restored": "✅ <b>Energía Restaurada</b>\nVoltaje de red: {voltage}V",
        "battery_full": "🔋 <b>Batería Completamente Cargada</b>\nNivel: 100%",
        "battery_discharging": "📉 <b>Batería Descargando</b>\nNivel: {level}%",
        "battery_low": "⚠️ <b>Batería Baja</b>\nNivel: {level}%",
        "status": "📊 <b>Estado del Sistema</b>\nRed: {grid_status}\nVoltaje: {voltage}V\nBatería: {battery}%",
        "online": "En línea 🟢",
        "offline": "Fuera de línea 🔴",
    },
    "de": {
        "start": "✅ Sie haben sich für Wechselrichter-Statusbenachrichtigungen angemeldet.",
        "stop": "❌ Sie haben sich von den Benachrichtigungen abgemeldet.",
        "select_lang": "Bitte wählen Sie Ihre Sprache:",
        "lang_set": "✅ Sprache auf Deutsch eingestellt.",
        "warning_battery": "⚠️ <b>Warnung: Batteriebetrieb</b>\nNetzspannung: {voltage}V",
        "power_restored": "✅ <b>Strom wiederhergestellt</b>\nNetzspannung: {voltage}V",
        "battery_full": "🔋 <b>Batterie voll geladen</b>\nStand: 100%",
        "battery_discharging": "📉 <b>Batterie entlädt</b>\nStand: {level}%",
        "battery_low": "⚠️ <b>Batteriestand niedrig</b>\nStand: {level}%",
        "status": "📊 <b>Systemstatus</b>\nNetz: {grid_status}\nSpannung: {voltage}V\nBatterie: {battery}%",
        "online": "Online 🟢",
        "offline": "Offline 🔴",
    }
}

LANGUAGES = {
    "en": "English",
    "uk": "Українська",
    "es": "Español",
    "de": "Deutsch"
}


def get_message(lang_code, key, **kwargs):
    lang = MESSAGES.get(lang_code, MESSAGES["en"])
    msg = lang.get(key, MESSAGES["en"].get(key, key))
    if kwargs:
        return msg.format(**kwargs)
    return msg
