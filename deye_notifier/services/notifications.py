"""Deliver persistent notification events to the appropriate subscribers."""

import asyncio
import json
import logging
from datetime import datetime

from aiogram.enums import ParseMode

from deye_notifier.bot import locales
from deye_notifier.database import chats as chat_repository
from deye_notifier.database import notifications as notification_repository

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, pool, bot, settings):
        self.pool = pool
        self.bot = bot
        self.settings = settings
        self.running = True

    async def deliver_pending(self):
        """Deliver durable events; leave failed events queued for the next attempt."""
        events = await notification_repository.get_pending_notifications(self.pool)
        if not events:
            return

        for event in events:
            if event["message_key"] == "statistics" and not self.settings.stats_auto_reports:
                await notification_repository.mark_notification_delivered(self.pool, event["id"])
                continue
            # Check preferences for each queued event, including events that
            # were created before a user opted out or before this upgrade.
            chats = await chat_repository.get_chats(
                self.pool, stats_only=event["message_key"] == "statistics"
            )
            all_sent = True
            for chat_id, language in chats:
                kwargs = {
                    "voltage": event["voltage"],
                    "load": event["load_watts"],
                    "duration": event["duration"],
                    "level": event["level"],
                    "threshold": event["threshold"] if event["threshold"] is not None else event["level"],
                    "forecast_line": locales.get_forecast_line(
                        language,
                        event["forecast_action"] or "",
                        event["forecast_text"] or "",
                    ),
                }
                if (
                    event["message_key"] in {"battery_discharging", "battery_low", "warning_battery"}
                    and event["level"] is not None
                ):
                    kwargs["runtime_line"] = locales.get_runtime_line(
                        language,
                        event["level"],
                        event["load_watts"],
                        self.settings.battery_capacity_kwh,
                        event["battery_cutoff_soc"]
                        if event.get("battery_cutoff_soc") is not None
                        else self.settings.battery_cutoff_soc,
                        self.settings.battery_inverter_efficiency,
                        include_energy=False,
                        runtime_seconds=event.get("runtime_eta_seconds"),
                    )
                if event["message_key"] == "battery_charging":
                    kwargs["charge_power_line"] = locales.get_charge_power_line(
                        language, event.get("charge_power_watts")
                    )
                    kwargs["charge_time_line"] = locales.get_charge_time_line(
                        language, event["charge_eta_seconds"]
                    )
                if event["report_payload"]:
                    payload = event["report_payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    text = locales.format_statistics(
                        language,
                        payload["period"],
                        datetime.fromisoformat(payload["start"]),
                        datetime.fromisoformat(payload["end"]),
                        payload["stats"],
                    )
                else:
                    text = locales.get_message(language, event["message_key"], **kwargs)
                sent = False
                for attempt in range(3):
                    try:
                        await self.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
                        sent = True
                        break
                    except Exception as e:
                        error_text = str(e)
                        if "bot was blocked by the user" in error_text or "chat not found" in error_text:
                            logger.warning(f"Removing unreachable chat {chat_id}: {e}")
                            try:
                                await chat_repository.remove_chat(self.pool, chat_id)
                            except Exception:
                                pass
                            sent = True
                            break
                        if attempt < 2:
                            await asyncio.sleep(2**attempt)
                        else:
                            logger.error(f"Failed to send queued event {event['id']} to {chat_id}: {e}")
                if not sent:
                    all_sent = False

            if all_sent:
                await notification_repository.mark_notification_delivered(self.pool, event["id"])

    async def delivery_loop(self):
        while self.running:
            try:
                await self.deliver_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Notification delivery loop failed; will retry")
            await asyncio.sleep(5)
