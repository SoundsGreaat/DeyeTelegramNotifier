"""Refresh one pinned message per opted-in chat using existing monitor data."""

import asyncio
import logging
import time
from datetime import datetime, timezone

from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from deye_notifier.bot import locales
from deye_notifier.bot.status import render_status
from deye_notifier.database import chats
from deye_notifier.database import live_status as repository

logger = logging.getLogger(__name__)


class NoTelemetryError(RuntimeError):
    pass


class LiveStatusService:
    def __init__(self, pool, bot, monitor, schedule_provider, settings):
        self.pool, self.bot, self.monitor = pool, bot, monitor
        self.schedule_provider, self.settings = schedule_provider, settings
        self._locks = {}
        self._last_text = {}
        self._paused_until = 0.0

    def _lock(self, chat_id):
        return self._locks.setdefault(chat_id, asyncio.Lock())

    def _render(self, language):
        reading = self.monitor.latest_reading
        if reading is None:
            raise NoTelemetryError("No inverter reading is available yet")
        action, forecast = self.schedule_provider.next_event_for_state(reading.is_grid_online)
        return render_status(
            language,
            reading,
            self.settings,
            state_since=self.monitor.last_status_time,
            charge_estimate=self.monitor.charging_estimate(),
            now=datetime.now(timezone.utc),
            forecast_action=action,
            forecast_text=forecast,
            detailed=True,
            observed_at=self.monitor.latest_reading_at,
        )

    async def _retire(self, entry, text_key):
        chat_id, message_id = entry["chat_id"], entry["message_id"]
        language = locales.normalize_language(entry["language"])
        old_text = self._last_text.pop((chat_id, message_id), "")
        stopped = locales.get_message(language, text_key)
        # Keep a cached final reading when available; it is explicitly frozen.
        text = old_text + "\n" + stopped if old_text else stopped
        try:
            await self.bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode=ParseMode.HTML,
                request_timeout=10,
            )
        except TelegramAPIError:
            logger.info("Could not mark retired live status in chat %s", chat_id)
        try:
            await self.bot.unpin_chat_message(chat_id=chat_id, message_id=message_id, request_timeout=10)
        except TelegramAPIError:
            logger.info("Could not unpin retired live status in chat %s", chat_id)

    async def enable(self, chat_id, language="en"):
        """Every explicit invocation replaces the active message atomically in DB."""
        async with self._lock(chat_id):
            await repository.ensure_preferences(self.pool, chat_id, locales.normalize_language(language))
            language = await chats.get_chat_language(self.pool, chat_id)
            text = self._render(language)
            old = await repository.get_entry(self.pool, chat_id)
            message = await self.bot.send_message(
                chat_id, text, parse_mode=ParseMode.HTML, disable_notification=True, request_timeout=10
            )
            try:
                await repository.save_entry(self.pool, chat_id, message.message_id)
            except Exception:
                try:
                    await self.bot.delete_message(chat_id, message.message_id, request_timeout=10)
                except TelegramAPIError:
                    logger.warning("Could not remove unregistered live status in chat %s", chat_id)
                raise
            if old is not None:
                await self._retire(old, "live_replaced")
            self._last_text[(chat_id, message.message_id)] = text
            try:
                await self.bot.pin_chat_message(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    disable_notification=True,
                    request_timeout=10,
                )
                return True
            except TelegramAPIError as error:
                if isinstance(error, TelegramRetryAfter):
                    self._paused_until = time.monotonic() + error.retry_after
                logger.info("Live status is active but pinning was not confirmed in chat %s", chat_id)
                return False

    async def stop(self, chat_id):
        async with self._lock(chat_id):
            entry = await repository.get_entry(self.pool, chat_id)
            if entry is not None:
                await repository.delete_entry(self.pool, chat_id, entry["message_id"])
                await self._retire(entry, "live_stopped")

    async def update_all(self):
        if time.monotonic() < self._paused_until:
            return
        for listed in await repository.get_entries(self.pool):
            chat_id = listed["chat_id"]
            async with self._lock(chat_id):
                # Commands may have replaced or stopped this entry since the list query.
                entry = await repository.get_entry(self.pool, chat_id)
                if entry is None:
                    continue
                key = (chat_id, entry["message_id"])
                try:
                    text = self._render(locales.normalize_language(entry["language"]))
                    if self._last_text.get(key) == text:
                        continue
                    await self.bot.edit_message_text(
                        text=text,
                        chat_id=chat_id,
                        message_id=entry["message_id"],
                        parse_mode=ParseMode.HTML,
                        request_timeout=10,
                    )
                    self._last_text[key] = text
                except NoTelemetryError:
                    continue
                except TelegramRetryAfter as error:
                    self._paused_until = time.monotonic() + error.retry_after
                    break
                except TelegramForbiddenError:
                    await repository.delete_entry(self.pool, chat_id, entry["message_id"])
                    self._last_text.pop(key, None)
                except TelegramBadRequest as error:
                    description = error.message.lower()
                    if "message is not modified" in description:
                        self._last_text[key] = text
                    elif (
                        "message to edit not found" in description or "message can't be edited" in description
                    ):
                        await repository.delete_entry(self.pool, chat_id, entry["message_id"])
                        self._last_text.pop(key, None)
                    else:
                        logger.warning("Live status edit rejected in chat %s", chat_id)
                except TelegramAPIError:
                    logger.warning("Live status update failed in chat %s; will retry", chat_id)

    async def run(self):
        while True:
            try:
                await self.update_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Could not refresh live status messages")
            await asyncio.sleep(self.settings.live_status_interval)
