"""Pure, observation-based estimates of battery charge completion time."""

import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ChargingEstimate:
    """Whether recent observations indicate charging and its remaining time."""

    is_charging: bool
    seconds_remaining: float | None


class ChargeEstimator:
    """Estimate charging from timestamped integer SOC samples.

    A charging session is deliberately local: discharge, a large calibration
    step, an invalid timestamp/value, or a polling gap starts a fresh baseline.
    The ETA uses a recency-weighted fit over at most ``window_seconds`` so it
    can follow rate changes rather than carrying a whole-session average.
    """

    def __init__(self, max_gap_seconds: float = 60, window_seconds: float = 900):
        if max_gap_seconds <= 0 or window_seconds <= 0:
            raise ValueError("gap and window durations must be positive")
        self.max_gap_seconds = float(max_gap_seconds)
        self.window_seconds = float(window_seconds)
        self._observations: list[tuple[datetime, int]] = []
        self._last_progress_at: datetime | None = None

    def _reset(self) -> None:
        self._observations.clear()
        self._last_progress_at = None

    @staticmethod
    def _valid_time(value: datetime) -> bool:
        return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None

    def observe(self, observed_at: datetime, battery_soc: int) -> None:
        """Add an observation, resetting the session on discontinuities."""
        if (
            not self._valid_time(observed_at)
            or isinstance(battery_soc, bool)
            or not isinstance(battery_soc, int)
            or not 0 <= battery_soc <= 100
        ):
            self._reset()
            return
        observed_at = observed_at.astimezone(timezone.utc)

        if not self._observations:
            self._observations.append((observed_at, battery_soc))
            return

        last_at, last_soc = self._observations[-1]
        elapsed = (observed_at - last_at).total_seconds()
        if elapsed <= 0:
            self._reset()
            return
        if elapsed > self.max_gap_seconds or battery_soc < last_soc or battery_soc - last_soc > 5:
            self._reset()
            self._observations.append((observed_at, battery_soc))
            return

        if battery_soc > last_soc:
            if (
                self._last_progress_at is None
                or (observed_at - self._last_progress_at).total_seconds() > self.window_seconds
            ):
                # Do not include a long idle period before charging starts.
                # Anchor at the first increase, avoiding a one-percent jump
                # over a single polling interval caused by integer SOC.
                self._observations = [(observed_at, battery_soc)]
                self._last_progress_at = observed_at
                return
            self._last_progress_at = observed_at
        self._observations.append((observed_at, battery_soc))

        # Keep a small boundary sample for the rolling regression. The list is
        # already bounded by the caller's polling cadence and this time window.
        cutoff = observed_at.timestamp() - self.window_seconds
        while len(self._observations) > 2 and self._observations[1][0].timestamp() < cutoff:
            self._observations.pop(0)

    def estimate(self, now: datetime) -> ChargingEstimate:
        """Return current charge state and an ETA when the data supports one."""
        if not self._valid_time(now):
            return ChargingEstimate(False, None)
        now = now.astimezone(timezone.utc)
        if not self._observations:
            return ChargingEstimate(False, None)

        latest_at, latest_soc = self._observations[-1]
        if latest_soc == 100:
            return ChargingEstimate(False, 0)
        age = (now - latest_at).total_seconds()
        # A future-dated reading cannot describe the state at ``now``.
        if age < 0 or age > self.max_gap_seconds:
            return ChargingEstimate(False, None)
        if self._last_progress_at is None:
            return ChargingEstimate(False, None)

        progress_age = (now - self._last_progress_at).total_seconds()
        if progress_age > self.window_seconds:
            return ChargingEstimate(False, None)

        # Fit only observations in the recent window. Warm up until at least
        # two integer SOC points have accumulated over a meaningful interval.
        cutoff = now.timestamp() - self.window_seconds
        points = [(at, soc) for at, soc in self._observations if at.timestamp() >= cutoff]
        if len(points) < 2:
            return ChargingEstimate(True, None)
        duration = (points[-1][0] - points[0][0]).total_seconds()
        net_progress = points[-1][1] - points[0][1]
        if duration < 60 or net_progress < 2:
            return ChargingEstimate(True, None)

        # Weighted least squares gives recent samples more influence (five
        # minute half-life), while retaining plateau samples to account for
        # integer SOC quantization and ordinary polling cadence.
        origin = points[-1][0].timestamp()
        xs = [at.timestamp() - origin for at, _ in points]
        ys = [float(soc) for _, soc in points]
        weights = [math.exp(-math.log(2) * max(0.0, -x) / 300.0) for x in xs]
        weight_sum = sum(weights)
        x_mean = sum(w * x for w, x in zip(weights, xs)) / weight_sum
        y_mean = sum(w * y for w, y in zip(weights, ys)) / weight_sum
        denominator = sum(w * (x - x_mean) ** 2 for w, x in zip(weights, xs))
        if denominator <= 0:
            return ChargingEstimate(True, None)
        rate_per_second = (
            sum(w * (x - x_mean) * (y - y_mean) for w, x, y in zip(weights, xs, ys)) / denominator
        )
        if rate_per_second <= 0:
            return ChargingEstimate(True, None)
        return ChargingEstimate(True, (100 - latest_soc) / rate_per_second)
