"""Telegram commands and router construction."""

import logging
from datetime import datetime, timezone

import asyncpg
from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

from deye_notifier import statistics
from deye_notifier.bot import locales
from deye_notifier.bot.status import render_status
from deye_notifier.config import Settings
from deye_notifier.database import chats as chat_repository
from deye_notifier.services import reports
from deye_notifier.services.live_status import NoTelemetryError
from deye_notifier.services.schedules import YasnoScheduleProvider

logger = logging.getLogger(__name__)


async def cmd_start(message: types.Message, pool: asyncpg.Pool):
    user_lang = (
        message.from_user.language_code if message.from_user.language_code in locales.LANGUAGES else "en"
    )
    await chat_repository.add_chat(pool, message.chat.id, user_lang)
    await message.answer(locales.get_message(user_lang, "start"))


async def cmd_stop(message: types.Message, pool: asyncpg.Pool, live_status_service=None):
    if live_status_service is not None:
        await live_status_service.stop(message.chat.id)
    await chat_repository.unsubscribe_chat(pool, message.chat.id)
    user_lang = (
        message.from_user.language_code if message.from_user.language_code in locales.LANGUAGES else "en"
    )
    await message.answer(locales.get_message(user_lang, "stop"))


async def cmd_stats_auto(
    message: types.Message, pool: asyncpg.Pool, command: CommandObject, settings: Settings
):
    user_lang = getattr(message.from_user, "language_code", None)
    if user_lang not in locales.LANGUAGES:
        user_lang = "en"
    enabled = command.command == "stats_auto_on"
    language = await chat_repository.set_stats_subscription(pool, message.chat.id, enabled, user_lang)
    text = locales.get_message(language, "stats_auto_enabled" if enabled else "stats_auto_disabled")
    if enabled and not settings.stats_auto_reports:
        text += "\n" + locales.get_message(language, "stats_auto_paused")
    await message.answer(text)


async def cmd_lang(message: types.Message):
    builder = InlineKeyboardBuilder()
    for code, name in locales.LANGUAGES.items():
        builder.button(text=name, callback_data=f"lang_{code}")
    builder.adjust(2)

    user_lang = (
        message.from_user.language_code if message.from_user.language_code in locales.LANGUAGES else "en"
    )
    await message.answer(locales.get_message(user_lang, "select_lang"), reply_markup=builder.as_markup())


async def callback_lang(callback: types.CallbackQuery, pool: asyncpg.Pool):
    lang_code = callback.data.split("_")[1]
    if lang_code == "ru":
        lang_code = "en"  # Accept old inline buttons without restoring the retired locale.
    if lang_code in locales.LANGUAGES:
        await chat_repository.set_chat_language(pool, callback.message.chat.id, lang_code)
        await callback.message.answer(locales.get_message(lang_code, "lang_set"))
        await callback.answer()
    else:
        await callback.answer("Invalid language")


async def cmd_status(
    message: types.Message,
    pool: asyncpg.Pool,
    schedule_provider: YasnoScheduleProvider,
    monitor,
    settings: Settings,
):
    user_lang = await chat_repository.get_chat_language(pool, message.chat.id)
    wait_msg = await message.answer("⏳")
    try:
        snapshot = await monitor.check()
    except Exception:
        logger.exception("Manual status check failed")
        snapshot = None
    if snapshot is None:
        await wait_msg.edit_text(locales.get_message(user_lang, "connection_failed"))
        return
    forecast_action, forecast_text = schedule_provider.next_event_for_state(snapshot.is_grid_online)
    text = render_status(
        user_lang,
        snapshot,
        settings,
        state_since=monitor.last_status_time,
        charge_estimate=monitor.charging_estimate(),
        now=datetime.now(timezone.utc),
        forecast_action=forecast_action,
        forecast_text=forecast_text,
    )

    await wait_msg.edit_text(text, parse_mode=ParseMode.HTML)


async def cmd_stats(message: types.Message, pool: asyncpg.Pool, command: CommandObject, settings: Settings):
    language = await chat_repository.get_chat_language(pool, message.chat.id)
    period = (
        command.command.removeprefix("stats_")
        if command.command != "stats"
        else (command.args or "day").strip().lower()
    )
    if period not in {"day", "week", "month"} or (command.command != "stats" and command.args):
        await message.answer(locales.get_message(language, "stats_usage"))
        return
    wait_msg = await message.answer("⏳")
    try:
        start, end = statistics.report_period(period, datetime.now(timezone.utc), settings.stats_timezone)
        stats = await reports.query_statistics(pool, start, end, settings)
        text = locales.format_statistics(language, period, start, end, stats)
    except Exception:
        logger.exception("Statistics command failed")
        text = locales.get_message(language, "stats_failed")
    await wait_msg.edit_text(text, parse_mode=ParseMode.HTML)


async def cmd_live(message: types.Message, pool: asyncpg.Pool, live_status_service):
    language = getattr(message.from_user, "language_code", "en")
    try:
        pinned = await live_status_service.enable(message.chat.id, language)
        if not pinned:
            language = await chat_repository.get_chat_language(pool, message.chat.id)
            await message.answer(locales.get_message(language, "live_pin_failed"))
    except NoTelemetryError:
        language = await chat_repository.get_chat_language(pool, message.chat.id)
        await message.answer(locales.get_message(language, "connection_failed"))
    except Exception:
        logger.exception("Could not enable live status")
        language = await chat_repository.get_chat_language(pool, message.chat.id)
        await message.answer(locales.get_message(language, "live_failed"))


async def cmd_live_off(message: types.Message, pool: asyncpg.Pool, live_status_service):
    language = await chat_repository.get_chat_language(pool, message.chat.id)
    try:
        await live_status_service.stop(message.chat.id)
        await message.answer(locales.get_message(language, "live_stopped"))
    except Exception:
        logger.exception("Could not stop live status")
        await message.answer(locales.get_message(language, "live_failed"))


def create_router():
    router = Router()
    router.message.register(cmd_start, Command("start"))
    router.message.register(cmd_stop, Command("stop"))
    router.message.register(cmd_stats_auto, Command("stats_auto_on", "stats_auto_off"))
    router.message.register(cmd_lang, Command("lang", "language"))
    router.callback_query.register(callback_lang, F.data.startswith("lang_"))
    router.message.register(cmd_status, Command("status"))
    router.message.register(cmd_live, Command("live"))
    router.message.register(cmd_live_off, Command("live_off"))
    router.message.register(cmd_stats, Command("stats", "stats_day", "stats_week", "stats_month"))
    return router
