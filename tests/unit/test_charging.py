import unittest
from datetime import datetime, timedelta, timezone

from deye_notifier.inverter.charging import ChargeEstimator, ChargingEstimate


class ChargeEstimatorTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.estimator = ChargeEstimator()

    def feed(self, values, spacing=60):
        for index, soc in enumerate(values):
            self.estimator.observe(self.start + timedelta(seconds=index * spacing), soc)
        return self.start + timedelta(seconds=(len(values) - 1) * spacing)

    def test_constant_integer_charge_rate_produces_eta(self):
        now = self.feed([50, 51, 52, 53])
        estimate = self.estimator.estimate(now)
        self.assertTrue(estimate.is_charging)
        self.assertAlmostEqual(estimate.seconds_remaining, 47 * 60, delta=15)

    def test_estimate_adapts_to_slower_and_faster_rates(self):
        # 1%/minute followed by 0.5%/minute: ETA grows after the slowdown.
        for i, soc in enumerate([40, 41, 42, 43, 44, 44, 45, 45, 46]):
            self.estimator.observe(self.start + timedelta(minutes=i), soc)
        before = self.estimator.estimate(self.start + timedelta(minutes=8))
        for minute, soc in [(9, 46), (10, 47), (11, 47), (12, 48)]:
            self.estimator.observe(self.start + timedelta(minutes=minute), soc)
        slower = self.estimator.estimate(self.start + timedelta(minutes=12))
        self.assertGreater(slower.seconds_remaining, before.seconds_remaining)

        # A later acceleration reduces ETA based on the recent samples.
        for minute, soc in [(13, 50), (14, 52), (15, 54)]:
            self.estimator.observe(self.start + timedelta(minutes=minute), soc)
        faster = self.estimator.estimate(self.start + timedelta(minutes=15))
        self.assertLess(faster.seconds_remaining, slower.seconds_remaining)

    def test_plateau_without_a_positive_trend_is_not_charging(self):
        now = self.feed([60, 60, 60, 60])
        self.assertEqual(self.estimator.estimate(now), ChargingEstimate(False, None))

    def test_plateau_after_progress_eventually_loses_numerical_eta(self):
        self.feed([60, 61, 62])
        for minute in range(3, 19):
            self.estimator.observe(self.start + timedelta(minutes=minute), 62)
        estimate = self.estimator.estimate(self.start + timedelta(minutes=18))
        self.assertEqual(estimate, ChargingEstimate(False, None))

    def test_discharge_resets_session(self):
        self.feed([60, 61, 62])
        now = self.start + timedelta(minutes=3)
        self.estimator.observe(now, 61)
        self.assertEqual(self.estimator.estimate(now), ChargingEstimate(False, None))

    def test_long_gap_resets_session(self):
        self.feed([60, 61])
        now = self.start + timedelta(minutes=3)
        self.estimator.observe(now, 62)
        self.assertEqual(self.estimator.estimate(now), ChargingEstimate(False, None))

    def test_large_positive_jump_resets_as_calibration(self):
        self.feed([60, 61])
        now = self.start + timedelta(minutes=2)
        self.estimator.observe(now, 70)
        self.assertEqual(self.estimator.estimate(now), ChargingEstimate(False, None))

    def test_warmup_reports_trend_without_eta_until_sixty_seconds_and_two_points(self):
        self.estimator.observe(self.start, 50)
        self.estimator.observe(self.start + timedelta(seconds=30), 51)
        self.assertEqual(
            self.estimator.estimate(self.start + timedelta(seconds=30)),
            ChargingEstimate(True, None),
        )
        self.estimator.observe(self.start + timedelta(seconds=60), 52)
        result = self.estimator.estimate(self.start + timedelta(seconds=60))
        self.assertTrue(result.is_charging)
        self.assertIsNone(result.seconds_remaining)
        self.estimator.observe(self.start + timedelta(seconds=90), 53)
        result = self.estimator.estimate(self.start + timedelta(seconds=90))
        self.assertIsNotNone(result.seconds_remaining)

    def test_idle_before_charging_does_not_reduce_the_measured_rate(self):
        for minute in range(21):
            self.estimator.observe(self.start + timedelta(minutes=minute), 50)
        for minute, soc in ((21, 51), (22, 52), (23, 53)):
            self.estimator.observe(self.start + timedelta(minutes=minute), soc)
        result = self.estimator.estimate(self.start + timedelta(minutes=23))
        self.assertAlmostEqual(result.seconds_remaining, 47 * 60, delta=1)

    def test_full_battery_has_zero_eta(self):
        self.estimator.observe(self.start, 100)
        self.assertEqual(self.estimator.estimate(self.start), ChargingEstimate(False, 0))

    def test_out_of_order_and_invalid_readings_clear_history(self):
        self.feed([50, 51, 52])
        older = self.start + timedelta(minutes=1, seconds=30)
        self.estimator.observe(older, 53)
        self.assertEqual(self.estimator.estimate(older), ChargingEstimate(False, None))
        self.estimator.observe(self.start + timedelta(minutes=3), 53)
        self.estimator.observe(self.start + timedelta(minutes=4), 101)
        self.estimator.observe(self.start + timedelta(minutes=5), 54)
        self.assertEqual(
            self.estimator.estimate(self.start + timedelta(minutes=5)),
            ChargingEstimate(False, None),
        )

    def test_stale_latest_sample_is_not_active(self):
        now = self.feed([50, 51, 52])
        self.assertEqual(
            self.estimator.estimate(now + timedelta(seconds=61)),
            ChargingEstimate(False, None),
        )


if __name__ == "__main__":
    unittest.main()
