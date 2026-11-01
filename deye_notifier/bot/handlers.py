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
from deye_notifier.config import Settings
from deye_notifier.database import chats as chat_repository
from deye_notifier.services import reports
from deye_notifier.services.schedules import YasnoScheduleProvider

logger = logging.getLogger(__name__)


async def cmd_start(message: types.Message, pool: asyncpg.Pool):
    user_lang = (
        message.from_user.language_code if message.from_user.language_code in locales.LANGUAGES else "en"
    )
    await chat_repository.add_chat(pool, message.chat.id, user_lang)
    await message.answer(locales.get_message(user_lang, "start"))


async def cmd_stop(message: types.Message, pool: asyncpg.Pool):
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
    grid_v, battery_soc, load_watts = snapshot.grid_voltage, snapshot.battery_soc, snapshot.load_watts
    is_online = snapshot.is_grid_online
    grid_status_text = locales.get_message(user_lang, "online" if is_online else "offline")
    forecast_action, forecast_text = schedule_provider.next_event_for_state(is_online)
    forecast_line = locales.get_forecast_line(user_lang, forecast_action, forecast_text)
    charge_estimate = monitor.charging_estimate()
    is_charging = snapshot.is_charging or charge_estimate.is_charging
    show_runtime = (
        not is_charging
        and settings.has_meaningful_load(load_watts)
        and (not is_online or snapshot.is_discharging)
    )

    text = locales.get_message(
        user_lang,
        "status",
        grid_status=grid_status_text,
        voltage=grid_v,
        voltage_line=locales.get_voltage_line(user_lang, grid_v) if is_online else "",
        battery=battery_soc,
        load=load_watts,
        state_duration_line=locales.get_state_duration_line(
            user_lang,
            is_online,
            (datetime.now(timezone.utc) - monitor.last_status_time).total_seconds(),
        ),
        runtime_line=locales.get_runtime_line(
            user_lang,
            battery_soc,
            load_watts,
            settings.battery_capacity_kwh,
            snapshot.cutoff_soc(settings),
            settings.battery_inverter_efficiency,
            runtime_seconds=snapshot.runtime_seconds(settings),
        )
        if show_runtime
        else "",
        charge_power_line=locales.get_charge_power_line(user_lang, snapshot.charge_power_watts),
        charge_time_line=locales.get_charge_time_line(user_lang, charge_estimate.seconds_remaining)
        if is_charging and battery_soc < 100
        else "",
        forecast_line=forecast_line,
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


def create_router():
    router = Router()
    router.message.register(cmd_start, Command("start"))
    router.message.register(cmd_stop, Command("stop"))
    router.message.register(cmd_stats_auto, Command("stats_auto_on", "stats_auto_off"))
    router.message.register(cmd_lang, Command("lang", "language"))
    router.callback_query.register(callback_lang, F.data.startswith("lang_"))
    router.message.register(cmd_status, Command("status"))
    router.message.register(cmd_stats, Command("stats", "stats_day", "stats_week", "stats_month"))
    return router
