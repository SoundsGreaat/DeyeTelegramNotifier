"""Poll inverter telemetry and persist state transitions and battery events."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from deye_notifier.database import monitor as state_repository
from deye_notifier.database import notifications as notification_repository
from deye_notifier.database import telemetry
from deye_notifier.database.connection import db_retry
from deye_notifier.inverter.charging import ChargeEstimator, ChargingEstimate
from deye_notifier.inverter.client import InverterClient

logger = logging.getLogger(__name__)
OUTAGE_LOAD_DELAY_SECONDS = 5


class DeyeStatusMonitor:
    def __init__(self, pool, schedule_provider, settings, client=None):
        self.pool = pool
        self.settings = settings
        self.client = client if client is not None else InverterClient(settings)
        self.schedule_provider = schedule_provider
        self.is_grid_online = None
        self.last_battery_soc = None
        self.running = True
        self.last_status_time = datetime.now(timezone.utc)
        self.check_lock = asyncio.Lock()
        self.charge_estimator = ChargeEstimator(max_gap_seconds=settings.stats_max_gap_seconds)
        self._charge_history_loaded = False
        self.latest_reading = None
        self.latest_reading_at = None
        self._load_followup_task = None

    def format_duration(self, delta):
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m {seconds}s"

    @staticmethod
    def battery_events(previous, current):
        """Describe every crossed 25% mark without alerting on the first read."""
        if previous is None or current is None:
            return []
        if current > previous:
            return [
                ("battery_full" if threshold == 100 else "battery_charging", threshold)
                for threshold in (25, 50, 75, 100)
                if previous < threshold <= current
            ]
        return [
            ("battery_low" if threshold == 25 else "battery_discharging", threshold)
            for threshold in (75, 50, 25)
            if current <= threshold < previous
        ]

    async def broadcast(self, key, **kwargs):
        await notification_repository.enqueue_notification(
            self.pool,
            key,
            voltage=kwargs.get("voltage"),
            load=kwargs.get("load", 0),
            duration=kwargs.get("duration", ""),
            level=kwargs.get("level"),
            threshold=kwargs.get("threshold"),
            forecast_text=kwargs.get("forecast_text", ""),
            charge_eta_seconds=kwargs.get("charge_eta_seconds"),
            charge_power_watts=kwargs.get("charge_power_watts"),
            runtime_eta_seconds=kwargs.get("runtime_eta_seconds"),
            battery_cutoff_soc=kwargs.get("battery_cutoff_soc"),
        )

    async def _cancel_load_followup(self):
        task = self._load_followup_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self._load_followup_task is task:
                self._load_followup_task = None

    async def close(self):
        self.running = False
        await self._cancel_load_followup()

    async def _schedule_load_followup(self):
        await self._cancel_load_followup()
        self._load_followup_task = asyncio.create_task(self._send_load_followup(self.last_status_time))

    async def _send_load_followup(self, outage_started):
        try:
            await asyncio.sleep(OUTAGE_LOAD_DELAY_SECONDS)
            async with self.check_lock:
                if (
                    not self.running
                    or self.is_grid_online is not False
                    or self.last_status_time != outage_started
                ):
                    return
                previous_soc = self.last_battery_soc
                reading = await self._check()
                events = self.battery_events(previous_soc, reading.battery_soc) if reading is not None else []
                # A normal discharge milestone already includes the settled load.
                load_already_sent = bool(events and events[-1][0] in {"battery_discharging", "battery_low"})
                if (
                    reading is not None
                    and not reading.is_grid_online
                    and self.settings.has_meaningful_load(reading.load_watts)
                    and not load_already_sent
                    and self.last_status_time == outage_started
                ):
                    await self.broadcast(
                        "outage_load",
                        load=reading.load_watts,
                        level=reading.battery_soc,
                        runtime_eta_seconds=reading.runtime_seconds(self.settings),
                        battery_cutoff_soc=reading.cutoff_soc(self.settings),
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not read settled load after power outage")
        finally:
            if self._load_followup_task is asyncio.current_task():
                self._load_followup_task = None

    def charging_estimate(self):
        now = datetime.now(timezone.utc)
        reading = self.latest_reading
        if reading is not None and (
            reading.battery_power_watts is not None or reading.battery_current is not None
        ):
            if (now - self.latest_reading_at).total_seconds() > self.settings.stats_max_gap_seconds:
                return ChargingEstimate(False, None)
            charging = reading.is_charging
            seconds = reading.battery_seconds(100) if charging and reading.battery_soc < 100 else None
            if charging and reading.battery_soc < 100 and seconds is None:
                trend = self.charge_estimator.estimate(now)
                seconds = trend.seconds_remaining if trend.is_charging else None
            return ChargingEstimate(charging, seconds)
        return self.charge_estimator.estimate(now)

    async def _observe_charge(self, now, battery_soc):
        if not self._charge_history_loaded:
            self._charge_history_loaded = True
            try:
                rows = await db_retry(
                    self.pool,
                    lambda: telemetry.fetch_samples(
                        self.pool,
                        now - timedelta(seconds=self.charge_estimator.window_seconds),
                        now,
                    ),
                )
                for row in rows:
                    if row["observed_at"] < now and row["battery_soc"] is not None:
                        self.charge_estimator.observe(row["observed_at"], row["battery_soc"])
            except Exception:
                logger.warning("Could not load charging history; collecting new observations")
        self.charge_estimator.observe(now, battery_soc)

    def read_inverter(self):
        return self.client.read_status()

    async def check(self):
        # Manual status requests share the polling path and its state/history.
        async with self.check_lock:
            return await self._check()

    async def _check(self):
        loop = asyncio.get_running_loop()
        try:
            reading = await loop.run_in_executor(None, self.read_inverter)
        except Exception as e:
            logger.error(f"Executor error: {e}")
            return

        if reading is None:
            return
        grid_v, battery_soc, load_watts = reading.grid_voltage, reading.battery_soc, reading.load_watts
        if battery_soc is None or not 0 <= battery_soc <= 100 or load_watts is None or load_watts < 0:
            logger.warning("Ignoring invalid inverter telemetry: SOC=%s load=%s", battery_soc, load_watts)
            return
        now = datetime.now(timezone.utc)
        self.latest_reading = reading
        self.latest_reading_at = now
        await self._observe_charge(now, battery_soc)
        currently_online = reading.is_grid_online
        runtime_seconds = reading.runtime_seconds(self.settings)
        cutoff = reading.cutoff_soc(self.settings)
        transitioned = False

        if self.is_grid_online is None:
            saved_state = await state_repository.load_monitor_state(self.pool)
            if saved_state is None:
                await state_repository.save_initial_monitor_state(self.pool, currently_online, now)
                self.is_grid_online = currently_online
                self.last_status_time = now
            else:
                self.is_grid_online = saved_state["is_grid_online"]
                self.last_status_time = saved_state["last_status_time"]
                if currently_online != self.is_grid_online:
                    duration = self.format_duration(now - self.last_status_time)
                    forecast_action, forecast_text = self.schedule_provider.next_event_for_state(
                        currently_online
                    )
                    await state_repository.record_transition(
                        self.pool,
                        currently_online,
                        now,
                        grid_v,
                        load_watts,
                        duration,
                        forecast_action,
                        forecast_text,
                        battery_soc=battery_soc,
                        runtime_eta_seconds=runtime_seconds,
                        battery_cutoff_soc=cutoff,
                    )
                    self.is_grid_online = currently_online
                    self.last_status_time = now
                    transitioned = True
            status_label = "GRID" if currently_online else "BATTERY"
            logger.info(
                f"Monitor started. Current mode: {status_label}, Voltage: {grid_v}V, Battery: {battery_soc}%"
            )
        elif currently_online != self.is_grid_online:
            duration = self.format_duration(now - self.last_status_time)
            forecast_action, forecast_text = self.schedule_provider.next_event_for_state(currently_online)
            await state_repository.record_transition(
                self.pool,
                currently_online,
                now,
                grid_v,
                load_watts,
                duration,
                forecast_action,
                forecast_text,
                battery_soc=battery_soc,
                runtime_eta_seconds=runtime_seconds,
                battery_cutoff_soc=cutoff,
            )
            self.last_status_time = now
            self.is_grid_online = currently_online
            transitioned = True
        else:
            status_text = "OK" if currently_online else "BATTERY"
            logger.debug(f"Grid: {grid_v}V, Batt: {battery_soc}% | {status_text}")

        if currently_online:
            await self._cancel_load_followup()
        elif transitioned and not self.settings.has_meaningful_load(load_watts):
            await self._schedule_load_followup()

        await db_retry(
            self.pool,
            lambda: telemetry.record_sample(
                self.pool,
                now,
                currently_online,
                load_watts,
                battery_soc,
                total_load_energy_kwh=reading.total_load_energy_kwh,
            ),
        )
        events = self.battery_events(self.last_battery_soc, battery_soc)
        # Several crossed milestones share the same current SOC. Report the
        # latest one once, with full/low battery taking precedence on a jump.
        for key, threshold in events[-1:]:
            await self.broadcast(
                key,
                level=battery_soc,
                threshold=threshold,
                load=load_watts,
                charge_eta_seconds=self.charging_estimate().seconds_remaining
                if key == "battery_charging"
                else None,
                runtime_eta_seconds=runtime_seconds,
                battery_cutoff_soc=cutoff,
                charge_power_watts=reading.charge_power_watts if key == "battery_charging" else None,
            )
            if self.settings.has_meaningful_load(load_watts) and key in {
                "battery_discharging",
                "battery_low",
            }:
                # The load has already reached subscribers in this milestone;
                # cancel a pending delayed update, including after manual checks.
                await self._cancel_load_followup()
        self.last_battery_soc = battery_soc
        return reading

    async def run(self):
        logger.info(f"Starting Deye Inverter Monitor for {self.settings.inverter_ip}")
        while self.running:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Monitor iteration failed; will retry")
            await asyncio.sleep(self.settings.check_interval)
