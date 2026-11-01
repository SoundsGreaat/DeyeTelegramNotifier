"""Telegram command menu registration."""

import asyncio
import logging

from aiogram import types
from aiogram.exceptions import TelegramAPIError

from deye_notifier.bot import locales

logger = logging.getLogger(__name__)


async def register_bot_commands(bot):
    commands = [
        types.BotCommand(command="start", description="Enable power and battery notifications"),
        types.BotCommand(command="stop", description="Disable all notifications and automatic reports"),
        types.BotCommand(command="status", description="Current status and estimated battery runtime"),
        types.BotCommand(command="live", description="Create a pinned status that updates automatically"),
        types.BotCommand(command="live_off", description="Stop and unpin the live status"),
        types.BotCommand(command="stats", description="Today's electricity statistics"),
        types.BotCommand(command="stats_day", description="Today's electricity statistics"),
        types.BotCommand(command="stats_week", description="This week's electricity statistics"),
        types.BotCommand(command="stats_month", description="This month's electricity statistics"),
        types.BotCommand(
            command="stats_auto_on", description="Enable automatic daily, weekly and monthly reports"
        ),
        types.BotCommand(command="stats_auto_off", description="Disable automatic reports"),
        types.BotCommand(command="lang", description="Choose the language of bot messages"),
    ]
    # The fallback serves other Telegram languages. Replace the supported
    # language overrides as well so an old localized menu cannot hide updates.
    for attempt in range(3):
        try:
            for language in ("", *locales.LANGUAGES):
                await bot.set_my_commands(
                    commands,
                    scope=types.BotCommandScopeDefault(),
                    language_code=language,
                    request_timeout=10,
                )
            # Retired language menus must fall back to the English default,
            # including after future changes to the command list.
            await bot.delete_my_commands(
                scope=types.BotCommandScopeDefault(), language_code="ru", request_timeout=10
            )
            logger.info("English command menu registered")
            return
        except TelegramAPIError as error:
            logger.warning(
                "Command menu registration failed (%s), attempt %s/3", type(error).__name__, attempt + 1
            )
            if attempt < 2:
                await asyncio.sleep(2**attempt)
    logger.warning("Could not register command menu; continuing bot startup")
