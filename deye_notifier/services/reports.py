"""Build period reports and enqueue each completed period once."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from deye_notifier import statistics
from deye_notifier.database import notifications as notification_repository
from deye_notifier.database.connection import db_retry

logger = logging.getLogger(__name__)


async def query_statistics(pool, start, end, settings):
    return await db_retry(
        pool,
        lambda: statistics.get_statistics(
            pool,
            start,
            end,
            settings.stats_max_gap_seconds,
        ),
    )


async def enqueue_completed_reports(pool, settings, now=None):
    local_now = (now or datetime.now(timezone.utc)).astimezone(settings.stats_timezone)
    eligible_time = local_now - timedelta(hours=settings.stats_report_hour)
    for period in ("day", "week", "month"):
        start, end = statistics.report_period(period, eligible_time, settings.stats_timezone, completed=True)
        report_key = f"{period}:{start.isoformat()}:{end.isoformat()}"
        if await notification_repository.report_exists(pool, report_key):
            continue
        stats = await query_statistics(pool, start, end, settings)
        if stats["coverage_percent"] <= 0:
            continue
        payload = json.dumps(
            {
                "period": period,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "stats": stats,
            }
        )
        await notification_repository.enqueue_report(pool, report_key, payload)


async def statistics_loop(pool, settings):
    if not settings.stats_auto_reports:
        return
    while True:
        try:
            await enqueue_completed_reports(pool, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not queue statistics reports; will retry")
        await asyncio.sleep(60)
