import json
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from support import IsolatedTestConfig, make_stats

from deye_notifier.services import reports


class ReportSchedulingTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    async def test_period_keys_survive_repeat_scheduler_and_respect_local_hour(self):
        queued = {}

        class Pool:
            @asynccontextmanager
            async def acquire(self):
                yield self

            async def fetchval(self, _query, key):
                return key in queued

            async def execute(self, _query, key, payload):
                queued.setdefault(key, json.loads(payload))

        pool = Pool()
        tz = ZoneInfo("Europe/Kyiv")
        settings = replace(self.settings, stats_timezone=tz, stats_report_hour=9)
        with patch.object(reports, "query_statistics", AsyncMock(return_value=make_stats())) as query:
            await reports.enqueue_completed_reports(pool, settings, datetime(2026, 6, 1, 8, 59, tzinfo=tz))
            before = [value for value in queued.values() if value["period"] == "day"][0]
            self.assertIn("2026-05-30", before["start"])
            queued.clear()
            await reports.enqueue_completed_reports(pool, settings, datetime(2026, 6, 1, 9, tzinfo=tz))
            self.assertEqual(len(queued), 3)
            periods = {value["period"]: value for value in queued.values()}
            self.assertIn("2026-05-31", periods["day"]["start"])
            self.assertIn("2026-05-25", periods["week"]["start"])
            self.assertIn("2026-05-01", periods["month"]["start"])
            calls = query.await_count
            await reports.enqueue_completed_reports(pool, settings, datetime(2026, 6, 1, 10, tzinfo=tz))
            self.assertEqual(query.await_count, calls)
            self.assertEqual(len(queued), 3)
