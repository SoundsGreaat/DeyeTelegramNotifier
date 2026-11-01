import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from support import IsolatedTestConfig

from deye_notifier.statistics import _build_intervals, get_statistics, report_period


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def asyncio_run(awaitable):
    return asyncio.run(awaitable)


class EnergyStatsTests(IsolatedTestConfig, unittest.TestCase):
    def test_intervals_clip_and_split_energy(self):
        rows = [
            (utc("2025-01-01T23:59:00"), True, 100, 90),
            (utc("2025-01-02T00:10:00"), False, 200, 80),
            (utc("2025-01-02T00:20:00"), False, 300, 70),
        ]
        intervals, unknown = _build_intervals(
            rows, utc("2025-01-02T00:00:00"), utc("2025-01-02T00:30:00"), 900
        )
        self.assertEqual(unknown, 600)
        self.assertEqual(sum(i[2] for i in intervals if i[3]), 600)
        self.assertEqual(sum(i[2] for i in intervals if not i[3]), 600)
        self.assertEqual(intervals[0][4], 100)

    def test_long_gap_is_unknown(self):
        rows = [
            (utc("2025-01-01T00:00:00"), True, 100, 90),
            (utc("2025-01-01T00:10:00"), False, 200, 80),
        ]
        intervals, unknown = _build_intervals(
            rows, utc("2025-01-01T00:00:00"), utc("2025-01-01T00:20:00"), 120
        )
        self.assertEqual(intervals, [])
        self.assertEqual(unknown, 1200)

    def test_numeric_statistics_and_successor_at_end(self):
        start, end = utc("2025-01-02T00:00:00"), utc("2025-01-02T00:30:00")
        rows = [
            {
                "observed_at": utc("2025-01-01T23:50:00"),
                "is_grid_online": True,
                "load_watts": 100,
                "battery_soc": 90,
            },
            {
                "observed_at": utc("2025-01-02T00:10:00"),
                "is_grid_online": False,
                "load_watts": 200,
                "battery_soc": 80,
            },
            {
                "observed_at": utc("2025-01-02T00:20:00"),
                "is_grid_online": False,
                "load_watts": 300,
                "battery_soc": 70,
            },
            {
                "observed_at": utc("2025-01-02T00:30:00"),
                "is_grid_online": True,
                "load_watts": 50,
                "battery_soc": 75,
            },
        ]

        async def fake_fetch(*_args):
            return rows

        async def run():
            with patch("deye_notifier.statistics.telemetry.fetch_samples", fake_fetch):
                return await get_statistics(None, start, end, max_gap_seconds=1800)

        stats = asyncio_run(run())
        self.assertAlmostEqual(stats["energy_kwh"], 0.1)
        self.assertEqual(stats["online_seconds"], 600)
        self.assertEqual(stats["offline_seconds"], 1200)
        self.assertEqual(stats["unknown_seconds"], 0)
        self.assertEqual(stats["outages"], 1)
        self.assertEqual(stats["longest_outage_seconds"], 1200)
        self.assertEqual(stats["peak_load_watts"], 300)
        self.assertEqual(stats["min_soc"], 70)
        self.assertEqual(stats["max_soc"], 80)

    def test_last_sample_has_peak_but_no_tail_energy(self):
        start, end = utc("2025-01-02T00:00:00"), utc("2025-01-02T00:30:00")
        rows = [{"observed_at": start, "is_grid_online": True, "load_watts": 400, "battery_soc": 60}]

        async def fake_fetch(*_args):
            return rows

        async def run():
            with patch("deye_notifier.statistics.telemetry.fetch_samples", fake_fetch):
                return await get_statistics(None, start, end)

        stats = asyncio_run(run())
        self.assertEqual(stats["energy_kwh"], 0)
        self.assertEqual(stats["unknown_seconds"], 1800)
        self.assertEqual(stats["peak_load_watts"], 400)

    def test_measured_counter_delta_is_used_and_clipped_proportionally(self):
        start, end = utc("2025-01-01T00:05:00"), utc("2025-01-01T00:15:00")
        rows = [
            {
                "observed_at": utc("2025-01-01T00:00:00"),
                "is_grid_online": True,
                "load_watts": 100,
                "battery_soc": 90,
                "total_load_energy_kwh": 10.0,
            },
            {
                "observed_at": utc("2025-01-01T00:10:00"),
                "is_grid_online": False,
                "load_watts": 200,
                "battery_soc": 80,
                "total_load_energy_kwh": 11.0,
            },
            {
                "observed_at": utc("2025-01-01T00:20:00"),
                "is_grid_online": False,
                "load_watts": 200,
                "battery_soc": 70,
                "total_load_energy_kwh": 12.0,
            },
        ]
        from deye_notifier.statistics import _calculate_statistics

        stats = _calculate_statistics(rows, start, end, 1200)
        self.assertAlmostEqual(stats["energy_kwh"], 1.0)
        self.assertAlmostEqual(stats["grid_energy_kwh"], 0.5)
        self.assertAlmostEqual(stats["battery_mode_energy_kwh"], 0.5)
        # Load averages and peaks retain their existing watt-based semantics.
        self.assertEqual(stats["avg_load_watts"], 150)
        self.assertEqual(stats["peak_load_watts"], 200)

    def test_old_rows_missing_counter_and_counter_reset_use_watt_fallback(self):
        start, end = utc("2025-01-01T00:00:00"), utc("2025-01-01T00:03:00")
        rows = [
            # Legacy four-field tuples remain supported.
            (start, True, 3600, 90),
            {
                "observed_at": utc("2025-01-01T00:01:00"),
                "is_grid_online": True,
                "load_watts": 1800,
                "battery_soc": 80,
                "total_load_energy_kwh": 5.0,
            },
            {
                "observed_at": utc("2025-01-01T00:02:00"),
                "is_grid_online": False,
                "load_watts": 900,
                "battery_soc": 70,
                "total_load_energy_kwh": None,
            },
            {
                "observed_at": end,
                "is_grid_online": False,
                "load_watts": 900,
                "battery_soc": 60,
                "total_load_energy_kwh": 4.0,
            },
        ]
        from deye_notifier.statistics import _calculate_statistics

        stats = _calculate_statistics(rows, start, end, 120)
        # All intervals fall back because at least one endpoint lacks a valid,
        # nondecreasing counter value.
        self.assertAlmostEqual(stats["energy_kwh"], 0.105)
        self.assertAlmostEqual(stats["grid_energy_kwh"], 0.09)
        self.assertAlmostEqual(stats["battery_mode_energy_kwh"], 0.015)

    def test_long_gap_with_counters_remains_unknown(self):
        from deye_notifier.statistics import _calculate_statistics

        start, end = utc("2025-01-01T00:00:00"), utc("2025-01-01T00:10:00")
        rows = [
            {
                "observed_at": start,
                "is_grid_online": True,
                "load_watts": 100,
                "battery_soc": None,
                "total_load_energy_kwh": 1.0,
            },
            {
                "observed_at": end,
                "is_grid_online": True,
                "load_watts": 100,
                "battery_soc": None,
                "total_load_energy_kwh": 2.0,
            },
        ]
        stats = _calculate_statistics(rows, start, end, 120)
        self.assertEqual(stats["energy_kwh"], 0)
        self.assertEqual(stats["unknown_seconds"], 600)

    def test_report_periods_and_completed_boundaries(self):
        try:
            tz = ZoneInfo("Europe/Kyiv")
        except ZoneInfoNotFoundError:
            self.skipTest("system tzdata is unavailable")
        now = datetime(2025, 5, 14, 15, 30, tzinfo=tz)
        day_start, day_end = report_period("day", now, tz)
        self.assertEqual(day_start.isoformat(), "2025-05-14T00:00:00+03:00")
        self.assertEqual(day_end, now)
        week_start, week_end = report_period("week", now, tz, completed=True)
        self.assertEqual(week_start.date().isoformat(), "2025-05-05")
        self.assertEqual(week_end.date().isoformat(), "2025-05-12")
        month_start, month_end = report_period("month", now, tz, completed=True)
        self.assertEqual(month_start.date().isoformat(), "2025-04-01")
        self.assertEqual(month_end.date().isoformat(), "2025-05-01")

    def test_report_period_dst_elapsed_duration(self):
        try:
            berlin = ZoneInfo("Europe/Berlin")
        except ZoneInfoNotFoundError:
            self.skipTest("system tzdata is unavailable")
        # The completed local day containing the spring transition is 23 hours.
        now = datetime(2025, 3, 31, 12, tzinfo=berlin)
        start, end = report_period("day", now, berlin, completed=True)
        elapsed = (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
        self.assertEqual(elapsed, 23 * 3600)

        # The completed local day containing the autumn transition is 25 hours.
        now = datetime(2025, 10, 27, 12, tzinfo=berlin)
        start, end = report_period("day", now, berlin, completed=True)
        elapsed = (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
        self.assertEqual(elapsed, 25 * 3600)

    def test_completed_month_rolls_back_across_year(self):
        try:
            tz = ZoneInfo("Europe/Berlin")
        except ZoneInfoNotFoundError:
            self.skipTest("system tzdata is unavailable")
        start, end = report_period("month", datetime(2025, 1, 3, 12, tzinfo=tz), tz, completed=True)
        self.assertEqual(start.date().isoformat(), "2024-12-01")
        self.assertEqual(end.date().isoformat(), "2025-01-01")


if __name__ == "__main__":
    unittest.main()
