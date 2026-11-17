"""Per-chat switches for automatic Telegram messages."""

import asyncio
import logging
from weakref import WeakValueDictionary

from aiogram import types
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.keyboard import InlineKeyboardBuilder

from deye_notifier.bot import locales
from deye_notifier.database import chats
from deye_notifier.notification_preferences import PREFERENCE_KEYS

logger = logging.getLogger(__name__)
CALLBACK_PREFIX = "notify:"
_panel_locks = WeakValueDictionary()

COPY = {
    "en": (
        "Automatic notifications",
        "Only chat administrators can change notifications.",
        "Open /notifications to use a fresh menu.",
        (
            "Power outage",
            "Power restored",
            "Load after outage",
            "High grid voltage",
            "Low grid voltage",
            "Grid voltage recovered",
            "Inverter disconnected",
            "Inverter reconnected",
            "Battery charging milestones",
            "Battery discharging milestones",
            "Battery low",
            "Battery fully charged",
            "Daily report",
            "Weekly report",
            "Monthly report",
        ),
    ),
    "uk": (
        "Автоматичні сповіщення",
        "Змінювати сповіщення можуть лише адміністратори чату.",
        "Відкрийте нове меню командою /notifications.",
        (
            "Світло вимкнено",
            "Світло увімкнено",
            "Навантаження після вимкнення",
            "Висока напруга мережі",
            "Низька напруга мережі",
            "Напруга відновилася",
            "Інвертор відключився",
            "Інвертор підключився",
            "Рівні заряду під час заряджання",
            "Рівні заряду під час розряджання",
            "Низький заряд",
            "Батарея повністю заряджена",
            "Денний звіт",
            "Тижневий звіт",
            "Місячний звіт",
        ),
    ),
    "es": (
        "Avisos automáticos",
        "Solo los administradores del chat pueden cambiar los avisos.",
        "Abre un menú nuevo con /notifications.",
        (
            "Corte eléctrico",
            "Suministro restablecido",
            "Carga tras el corte",
            "Voltaje de red alto",
            "Voltaje de red bajo",
            "Voltaje recuperado",
            "Inversor desconectado",
            "Inversor reconectado",
            "Niveles de carga de batería",
            "Niveles de descarga de batería",
            "Batería baja",
            "Batería al 100 %",
            "Informe diario",
            "Informe semanal",
            "Informe mensual",
        ),
    ),
    "de": (
        "Automatische Benachrichtigungen",
        "Nur Chat-Administratoren können Benachrichtigungen ändern.",
        "Öffne mit /notifications ein neues Menü.",
        (
            "Stromausfall",
            "Strom wieder da",
            "Last nach Stromausfall",
            "Hohe Netzspannung",
            "Niedrige Netzspannung",
            "Netzspannung wieder normal",
            "Wechselrichter getrennt",
            "Wechselrichter wieder verbunden",
            "Ladestufen der Batterie",
            "Entladestufen der Batterie",
            "Niedriger Batteriestand",
            "Batterie voll geladen",
            "Tagesbericht",
            "Wochenbericht",
            "Monatsbericht",
        ),
    ),
}


def _panel(language, preferences, settings):
    title, _, _, labels = COPY[language]
    builder = InlineKeyboardBuilder()
    for key, label in zip(PREFERENCE_KEYS, labels, strict=True):
        enabled = preferences[key]
        icon = "✅" if enabled else "❌"
        builder.button(text=f"{icon} {label}", callback_data=f"{CALLBACK_PREFIX}{key}:{int(not enabled)}")
    builder.adjust(1)
    if not settings.stats_auto_reports:
        title += "\n" + locales.get_message(language, "stats_auto_paused")
    return title, builder.as_markup()


async def can_manage_notifications(chat, user, bot):
    if chat.type == "private":
        return True
    if user is None or chat.type not in {"group", "supergroup"}:
        return False
    try:
        member = await bot.get_chat_member(chat.id, user.id, request_timeout=5)
    except TelegramAPIError:
        return False
    return member.status in {"administrator", "creator"}


async def allow_notification_command(message, pool):
    if await can_manage_notifications(message.chat, message.from_user, message.bot):
        return True
    language = await chats.get_chat_language(pool, message.chat.id)
    await message.answer(COPY[language][1])
    return False


async def cmd_notifications(message: types.Message, pool, settings):
    language, preferences = await chats.get_notification_preferences(
        pool, message.chat.id, getattr(message.from_user, "language_code", None)
    )
    title, markup = _panel(language, preferences, settings)
    await message.answer(title, reply_markup=markup)


async def callback_notification(callback: types.CallbackQuery, pool, settings):
    message = callback.message
    language = locales.normalize_language(callback.from_user.language_code)
    key, _, value = (callback.data or "").removeprefix(CALLBACK_PREFIX).partition(":")
    if not isinstance(message, types.Message) or key not in PREFERENCE_KEYS or value not in {"0", "1"}:
        await callback.answer(COPY[language][2], show_alert=True)
        return
    if not await can_manage_notifications(message.chat, callback.from_user, callback.bot):
        language = await chats.get_chat_language(pool, message.chat.id)
        await callback.answer(COPY[language][1], show_alert=True)
        return

    # Keep saves and edits ordered when multiple users press the same panel.
    lock = _panel_locks.setdefault((message.chat.id, message.message_id), asyncio.Lock())
    async with lock:
        language, preferences = await chats.set_notification_preference(
            pool, message.chat.id, key, value == "1", language
        )
        title, markup = _panel(language, preferences, settings)
        try:
            await callback.answer()
        except TelegramAPIError:
            logger.debug("Could not acknowledge notification settings button")
        try:
            await message.edit_text(title, reply_markup=markup)
        except TelegramAPIError as error:
            if "message is not modified" not in str(error).lower():
                logger.warning("Could not refresh notification settings menu (%s)", type(error).__name__)
