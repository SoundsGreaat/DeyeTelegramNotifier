"""Optional outage schedule provider and refresh loop."""

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from deye_notifier.database import schedules as schedule_repository

logger = logging.getLogger(__name__)


class YasnoScheduleProvider:
    """Optional Kyiv/YASNO adapter; disabled unless all schedule settings are provided."""

    API_BASE = "https://app.yasno.ua/api/blackout-service/public/shutdowns"
    OUTAGE_TYPES = {"Definite", "Likely", "Probable", "Emergency"}

    def __init__(self, pool, settings):
        self.pool = pool
        self.settings = settings
        self.enabled = (
            self.settings.schedule_provider == "yasno"
            and bool(self.settings.schedule_region_id)
            and bool(self.settings.schedule_dso_id)
            and bool(self.settings.schedule_street)
            and bool(self.settings.schedule_house)
        )
        self.cache_key = "|".join(
            [
                self.settings.schedule_provider,
                self.settings.schedule_region_id,
                self.settings.schedule_dso_id,
                self.settings.schedule_city_id,
                self.settings.schedule_street.casefold(),
                self.settings.schedule_house.casefold(),
            ]
        )
        try:
            self.timezone = ZoneInfo(self.settings.schedule_timezone)
        except Exception:
            logger.warning(
                "Invalid SCHEDULE_TIMEZONE=%s; using local timezone", self.settings.schedule_timezone
            )
            self.timezone = datetime.now().astimezone().tzinfo
        self.schedule = None
        self.group = None

    def _params(self, **kwargs):
        params = dict(kwargs)
        params["regionId"] = self.settings.schedule_region_id
        params["dsoId"] = self.settings.schedule_dso_id
        if self.settings.schedule_city_id:
            params["cityId"] = self.settings.schedule_city_id
        return params

    def _get_json(self, path, params):
        url = f"{self.API_BASE}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "DeyeTelegramNotifier/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=self.settings.schedule_request_timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _select_value(items, wanted):
        if not items:
            return None
        wanted = wanted.casefold().strip()
        exact = next(
            (item for item in items if str(item.get("value", "")).casefold().strip() == wanted), None
        )
        if exact:
            return exact
        return next((item for item in items if wanted in str(item.get("value", "")).casefold()), None)

    def _resolve_group(self):
        street_response = self._get_json(
            "/addresses/v2/streets", self._params(query=self.settings.schedule_street)
        )
        street = self._select_value(street_response, self.settings.schedule_street)
        if not street:
            raise RuntimeError(f"Street not found: {self.settings.schedule_street}")

        house_response = self._get_json(
            "/addresses/v2/houses",
            self._params(streetId=street["id"], query=self.settings.schedule_house),
        )
        house = self._select_value(house_response, self.settings.schedule_house)
        if not house:
            raise RuntimeError(f"House not found: {self.settings.schedule_house}")

        group_response = self._get_json(
            "/addresses/v2/group",
            self._params(streetId=street["id"], houseId=house["id"]),
        )
        if isinstance(group_response, (str, int)):
            return str(group_response)
        group = group_response.get("group")
        subgroup = group_response.get("subgroup") or 0
        if group is None:
            raise RuntimeError(f"No schedule group for house: {self.settings.schedule_house}")
        return f"{group}.{subgroup}" if subgroup else str(group)

    def _fetch_schedule(self):
        group = self._resolve_group()
        schedules = self._get_json(
            f"/regions/{self.settings.schedule_region_id}/dsos/{self.settings.schedule_dso_id}/planned-outages",
            {},
        )
        group_schedule = schedules.get(group)
        if not group_schedule:
            raise RuntimeError(f"Schedule group {group} was not returned by provider")
        return {
            "group": group,
            "today": group_schedule.get("today"),
            "tomorrow": group_schedule.get("tomorrow"),
            "updated_on": group_schedule.get("updatedOn"),
        }

    async def load_cache(self):
        if not self.enabled:
            return
        try:
            self.schedule = await schedule_repository.load_schedule_cache(self.pool, self.cache_key)
            if self.schedule:
                self.group = self.schedule.get("group")
        except Exception:
            logger.exception("Could not load schedule cache")

    async def refresh(self):
        if not self.enabled:
            return
        try:
            payload = await asyncio.to_thread(self._fetch_schedule)
            self.schedule = payload
            self.group = payload.get("group")
            await schedule_repository.save_schedule_cache(self.pool, self.cache_key, payload)
            logger.info(
                "Schedule updated: provider=yasno group=%s updated=%s", self.group, payload.get("updated_on")
            )
        except Exception as e:
            logger.warning("Schedule update failed: %s", e)

    async def run(self):
        if not self.enabled:
            logger.info("Schedule forecasts disabled: SCHEDULE_PROVIDER is not configured")
            return
        await self.load_cache()
        while True:
            await self.refresh()
            await asyncio.sleep(self.settings.schedule_refresh_interval)

    @staticmethod
    def _day_start(day_payload, fallback_date, timezone):
        raw_date = (day_payload or {}).get("date")
        if raw_date:
            try:
                return (
                    datetime.fromisoformat(raw_date)
                    .astimezone(timezone)
                    .replace(hour=0, minute=0, second=0, microsecond=0)
                )
            except ValueError:
                pass
        return datetime.combine(fallback_date, datetime.min.time(), tzinfo=timezone)

    @staticmethod
    def _format_range(start, end):
        if start.date() == end.date():
            return f"{start:%d.%m.%Y %H:%M}–{end:%H:%M}"
        return f"{start:%d.%m.%Y %H:%M}–{end:%d.%m.%Y %H:%M}"

    def next_event_for_state(self, is_grid_online):
        """Return (explicit action, local time) for the next scheduled transition.

        The inverter state remains authoritative; the schedule only describes what
        the provider expects to happen next.
        """
        if not self.enabled or not self.schedule:
            return "", ""
        now = datetime.now(self.timezone)
        for offset, key in enumerate(("today", "tomorrow")):
            day_payload = self.schedule.get(key) or {}
            day_start = self._day_start(day_payload, now.date() + timedelta(days=offset), self.timezone)
            for slot in day_payload.get("slots", []):
                start = day_start + timedelta(minutes=int(slot["start"]))
                end = day_start + timedelta(minutes=int(slot["end"]))
                is_outage = slot.get("type") in self.OUTAGE_TYPES
                if is_grid_online and is_outage and start >= now:
                    return "turn_off", self._format_range(start, end)
                if not is_grid_online and is_outage and start <= now < end:
                    return "turn_on", f"{end:%d.%m.%Y %H:%M}"
        return "", ""
