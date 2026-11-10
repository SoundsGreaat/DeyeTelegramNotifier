"""Compose application dependencies and manage their lifecycle."""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher

from deye_notifier.api import ApiServer, create_api
from deye_notifier.bot.handlers import create_router
from deye_notifier.bot.menu import register_bot_commands
from deye_notifier.config import Settings
from deye_notifier.database.connection import get_db_pool
from deye_notifier.database.migrations import prepare_database
from deye_notifier.inverter.monitor import DeyeStatusMonitor
from deye_notifier.services.live_status import LiveStatusService
from deye_notifier.services.notifications import NotificationService
from deye_notifier.services.reports import statistics_loop
from deye_notifier.services.schedules import YasnoScheduleProvider


def create_dispatcher():
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    return dispatcher


async def main(settings=None):
    settings = settings or Settings.from_env()
    if not settings.telegram_token:
        raise ValueError("TELEGRAM_TOKEN is required")

    bot = Bot(token=settings.telegram_token)
    pool = None
    monitor = notifier = None
    api_server = None
    tasks = []
    try:
        pool = await get_db_pool(settings)
        await prepare_database(settings)
        await register_bot_commands(bot)
        schedule_provider = YasnoScheduleProvider(pool, settings)
        monitor = DeyeStatusMonitor(pool, schedule_provider, settings)
        notifier = NotificationService(pool, bot, settings)
        live_status_service = LiveStatusService(pool, bot, monitor, schedule_provider, settings)
        if settings.api_enabled:
            api_server = ApiServer(create_api(pool, monitor, schedule_provider, settings), settings)
            await api_server.start()
        tasks = [
            asyncio.create_task(monitor.run()),
            asyncio.create_task(notifier.delivery_loop()),
            asyncio.create_task(live_status_service.run()),
            asyncio.create_task(schedule_provider.run()),
            asyncio.create_task(statistics_loop(pool, settings)),
        ]
        polling = asyncio.create_task(
            create_dispatcher().start_polling(
                bot,
                pool=pool,
                schedule_provider=schedule_provider,
                monitor=monitor,
                live_status_service=live_status_service,
                settings=settings,
                close_bot_session=False,
            )
        )
        tasks.append(polling)
        if api_server is None:
            await polling
        else:
            api_waiter = asyncio.create_task(api_server.wait())
            tasks.append(api_waiter)
            done, _ = await asyncio.wait((polling, api_waiter), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if polling not in done:
                raise RuntimeError("API server stopped unexpectedly")
    finally:
        if monitor is not None:
            monitor.running = False
        if notifier is not None:
            notifier.running = False
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            try:
                if api_server is not None:
                    await api_server.close()
            finally:
                if monitor is not None:
                    await monitor.close()
        finally:
            try:
                if pool is not None:
                    await pool.close()
            finally:
                await bot.session.close()


def run():
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    asyncio.run(main(settings))
