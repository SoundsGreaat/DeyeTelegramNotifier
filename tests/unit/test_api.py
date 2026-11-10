"""API tests use in-process ASGI requests and synthetic measurements only."""

import base64
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import httpx
from support import IsolatedTestConfig, make_stats

from deye_notifier.api import create_api
from deye_notifier.database import api as repository
from deye_notifier.database import telemetry
from deye_notifier.inverter.charging import ChargingEstimate
from deye_notifier.inverter.client import InverterReading

TOKEN = "synthetic-api-token-for-testing-123456"


class ApiTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = replace(self.settings, api_enabled=True, api_token=TOKEN)
        now = datetime.now(UTC)
        self.reading = InverterReading(280, False, 80, 500, 10, 200, 20, 123.4, 540, 54, 21, 50, 44)
        self.monitor = SimpleNamespace(
            latest_reading=self.reading,
            latest_reading_at=now,
            last_status_time=now - timedelta(hours=2),
            is_grid_online=True,
            charging_estimate=Mock(return_value=ChargingEstimate(False, None)),
            check=AsyncMock(side_effect=AssertionError("API must not poll inverter")),
            read_inverter=Mock(side_effect=AssertionError("API must not read inverter")),
        )
        self.provider = SimpleNamespace(
            enabled=False,
            schedule=None,
            timezone=ZoneInfo("Europe/Kyiv"),
            OUTAGE_TYPES={"Definite", "Likely", "Probable", "Emergency"},
            refresh=AsyncMock(side_effect=AssertionError("API must not refresh external schedule")),
        )
        self.app = create_api(None, self.monitor, self.provider, self.settings)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.addAsyncCleanup(self.client.aclose)

    def period(self):
        now = datetime.now(UTC)
        return {"start": (now - timedelta(hours=1)).isoformat(), "end": now.isoformat()}

    async def test_every_route_and_documentation_require_authentication_before_validation(self):
        for path in list(self.app.openapi()["paths"]) + ["/docs", "/openapi.json", "/redoc", "/unknown"]:
            for authorization in ("", "Bearer wrong", "Basic invalid"):
                with self.subTest(path=path, authorization=authorization):
                    response = await self.client.get(path, headers={"Authorization": authorization})
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertNotIn(TOKEN, response.text)
        response = await self.client.get(
            "/api/v1/status", params={"api_token": TOKEN}, headers={"Authorization": ""}
        )
        self.assertEqual(response.status_code, 401)

    async def test_docs_allow_browser_basic_but_api_requires_bearer(self):
        value = base64.b64encode(f"api:{TOKEN}".encode()).decode()
        basic = {"Authorization": f"Basic {value}"}
        for path in ("/docs", "/openapi.json"):
            response = await self.client.get(path, headers=basic)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(TOKEN, response.text)
        response = await self.client.get("/api/v1/status", headers=basic)
        self.assertEqual(response.status_code, 401)
        schema = (await self.client.get("/openapi.json")).json()
        self.assertEqual(schema["components"]["securitySchemes"]["ApiToken"]["scheme"], "bearer")
        for operations in schema["paths"].values():
            self.assertEqual(operations["get"]["security"], [{"ApiToken": []}])

    async def test_write_methods_are_blocked_without_side_effects(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            response = await self.client.request(method, "/api/v1/status", json={"write": "never"})
            self.assertEqual(response.status_code, 405)
        self.monitor.check.assert_not_awaited()
        self.monitor.read_inverter.assert_not_called()

    async def test_status_preserves_grid_relay_distinction_and_has_numeric_estimates(self):
        response = await self.client.get("/api/v1/status")
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertTrue(value["grid_present"])
        self.assertFalse(value["grid_connected"])
        self.assertEqual(value["grid_voltage_state"], "high")
        self.assertEqual(value["battery_temperature_c"], 21)
        self.assertEqual(value["runtime_eta_seconds"], 43200)
        self.assertEqual(value["cutoff_source"], "inverter")
        self.assertFalse(value["stale"])
        self.assertGreaterEqual(value["grid_state_duration_seconds"], 7200)
        self.monitor.check.assert_not_awaited()
        self.monitor.read_inverter.assert_not_called()

    async def test_missing_and_stale_telemetry_are_honest(self):
        self.monitor.latest_reading_at = datetime.now(UTC) - timedelta(minutes=5)
        response = await self.client.get("/api/v1/status")
        value = response.json()
        self.assertTrue(value["stale"])
        self.assertIsNone(value["runtime_eta_seconds"])
        self.assertIsNone(value["charge_eta_seconds"])
        expected = (self.monitor.latest_reading_at - self.monitor.last_status_time).total_seconds()
        self.assertAlmostEqual(value["grid_state_duration_seconds"], expected)
        self.monitor.latest_reading = None
        response = await self.client.get("/api/v1/status")
        self.assertEqual(response.status_code, 503)

    async def test_charging_and_full_battery_do_not_get_discharge_or_zero_charge_forecasts(self):
        for soc, expected in ((80, 1800), (100, None)):
            self.monitor.latest_reading = replace(
                self.reading, battery_soc=soc, battery_current=-80, battery_power_watts=-4000
            )
            self.monitor.charging_estimate.return_value = ChargingEstimate(True, 1800)
            value = (await self.client.get("/api/v1/status")).json()
            self.assertTrue(value["charging"])
            self.assertIsNone(value["runtime_eta_seconds"])
            self.assertEqual(value["charge_eta_seconds"], expected)
        self.monitor.latest_reading = replace(self.reading, battery_current=0, battery_power_watts=0)
        self.monitor.charging_estimate.return_value = ChargingEstimate(False, None)
        self.assertIsNone((await self.client.get("/api/v1/status")).json()["runtime_eta_seconds"])

    async def test_history_pagination_preserves_old_null_values_and_removes_private_fields(self):
        period = self.period()
        observed = datetime.fromisoformat(period["start"])
        row = {
            "observed_at": observed,
            "is_grid_online": True,
            "load_watts": 500,
            "battery_soc": 80,
            "api_token": TOKEN,
            "chat_id": 123,
        }
        with patch.object(
            repository,
            "fetch_history",
            AsyncMock(return_value=[row, row | {"observed_at": observed + timedelta(seconds=10)}]),
        ) as fetch:
            response = await self.client.get("/api/v1/history", params=period | {"limit": 1})
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertEqual(len(value["items"]), 1)
        self.assertIsNone(value["items"][0]["battery_temperature_c"])
        self.assertEqual(datetime.fromisoformat(value["next_cursor"]), observed)
        self.assertNotIn(TOKEN, response.text)
        self.assertNotIn("chat_id", response.text)
        self.assertEqual(fetch.await_args.args[-1], 1)

    async def test_history_rejects_unbounded_and_invalid_queries_without_echoing_input(self):
        period = self.period()
        with patch.object(repository, "fetch_history", AsyncMock()) as fetch:
            for params in (
                period | {"limit": 1001},
                period | {"limit": 0},
                period | {"start": TOKEN},
                period | {"start": "2026-01-01"},
                period | {"start": "2025-01-01T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
                period | {"end": period["start"]},
                period | {"cursor": "2020-01-01T00:00:00Z"},
            ):
                with self.subTest(params=params):
                    response = await self.client.get("/api/v1/history", params=params)
                    self.assertEqual(response.status_code, 422)
                    self.assertNotIn(TOKEN, response.text)
            fetch.assert_not_awaited()

    async def test_aggregate_has_observed_counts_and_nulls_without_gap_filling(self):
        observed = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        row = {
            "bucket_start": observed,
            "sample_count": 2,
            "first_observed_at": observed,
            "last_observed_at": observed + timedelta(seconds=10),
            "grid_present_fraction": 0.5,
        }
        row.update(
            {
                f"{name}_{operation}": None
                for name in repository.AGGREGATE_COLUMNS
                for operation in ("avg", "min", "max")
            }
        )
        row["load_watts_avg"] = 500
        with patch.object(repository, "fetch_aggregate", AsyncMock(return_value=[row])):
            response = await self.client.get("/api/v1/history/aggregate", params=self.period())
        value = response.json()
        self.assertEqual(len(value["items"]), 1)
        self.assertEqual(value["items"][0]["sample_count"], 2)
        self.assertIsNone(value["items"][0]["measurements"]["battery_temperature_c"]["avg"])
        with patch.object(repository, "fetch_aggregate", AsyncMock()) as fetch:
            response = await self.client.get(
                "/api/v1/history/aggregate",
                params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-31T00:00:00Z", "interval": "minute"},
            )
            self.assertEqual(response.status_code, 422)
            fetch.assert_not_awaited()

    async def test_statistics_reuse_existing_calculation_and_validate_custom_ranges(self):
        with patch("deye_notifier.api.app.query_statistics", AsyncMock(return_value=make_stats())) as query:
            response = await self.client.get(
                "/api/v1/statistics", params={"period": "month", "completed": True}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["statistics"]["energy_kwh"], 3.5)
            self.assertEqual(query.await_count, 1)
            response = await self.client.get("/api/v1/statistics", params=self.period())
            self.assertEqual(response.json()["period"], "custom")
            self.assertEqual(
                (
                    await self.client.get("/api/v1/statistics", params={"start": self.period()["start"]})
                ).status_code,
                422,
            )

    async def test_database_errors_are_generic_and_do_not_expose_credentials(self):
        with patch.object(
            repository, "fetch_history", AsyncMock(side_effect=RuntimeError(f"database password {TOKEN}"))
        ):
            response = await self.client.get("/api/v1/history", params=self.period())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(TOKEN, response.text)
        self.assertNotIn("password", response.text)

    async def test_empty_and_single_point_statistics_return_unknown_loads_without_server_error(self):
        period = self.period()
        observed = datetime.fromisoformat(period["start"]) + timedelta(seconds=10)
        point = dict(
            observed_at=observed,
            is_grid_online=True,
            load_watts=500,
            battery_soc=80,
            total_load_energy_kwh=123,
        )
        for rows, peak in (([], None), ([point], 500)):
            with (
                self.subTest(rows=rows),
                patch.object(telemetry, "fetch_samples", AsyncMock(return_value=rows)),
            ):
                response = await self.client.get("/api/v1/statistics", params=period)
            self.assertEqual(response.status_code, 200)
            values = response.json()["statistics"]
            self.assertEqual(values["coverage_percent"], 0)
            self.assertIsNone(values["avg_load_watts"])
            self.assertEqual(values["peak_load_watts"], peak)
            self.assertEqual(values["grid_energy_kwh"], 0)
            self.assertEqual(values["battery_mode_energy_kwh"], 0)

    async def test_events_are_paged_and_response_does_not_leak_report_payloads(self):
        row = dict(
            id=1,
            message_key="grid_voltage_high",
            created_at=datetime.now(UTC),
            voltage=280,
            load_watts=500,
            level=80,
            threshold=None,
            duration="",
            runtime_eta_seconds=None,
            charge_eta_seconds=None,
            charge_power_watts=None,
            battery_cutoff_soc=20,
            report_payload={"secret": TOKEN},
            forecast_text="private address",
        )
        with patch.object(repository, "fetch_events", AsyncMock(return_value=[row, row | {"id": 2}])):
            response = await self.client.get("/api/v1/events", params={"limit": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["next_cursor"], 1)
        self.assertNotIn(TOKEN, response.text)
        self.assertNotIn("private address", response.text)

    async def test_schedule_is_cached_normalized_and_address_free(self):
        now = datetime.now(self.provider.timezone)
        self.provider.enabled = True
        self.provider.schedule = {
            "today": {
                "date": now.date().isoformat(),
                "slots": [{"start": 600, "end": 660, "type": "Definite"}],
            },
            "group": "1.1",
            "updated_on": now.isoformat(),
            "street": "private address",
            "tomorrow": None,
        }
        response = await self.client.get("/api/v1/schedule")
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertTrue(value["available"])
        self.assertEqual(value["group"], "1.1")
        self.assertEqual(len(value["slots"]), 1)
        self.assertIsNotNone(datetime.fromisoformat(value["slots"][0]["start"]).tzinfo)
        self.assertNotIn("private address", response.text)
        self.provider.refresh.assert_not_awaited()
        self.provider.schedule["today"]["date"] = (now - timedelta(days=3)).date().isoformat()
        self.assertFalse((await self.client.get("/api/v1/schedule")).json()["available"])

    async def test_health_and_configuration_only_expose_public_data(self):
        with patch.object(repository, "database_available", AsyncMock(return_value=True)):
            self.assertEqual((await self.client.get("/api/v1/health")).status_code, 200)
            self.monitor.latest_reading_at = datetime.now(UTC) - timedelta(minutes=5)
            response = await self.client.get("/api/v1/health")
            self.assertEqual(response.status_code, 503)
            self.assertTrue(response.json()["stale"])
        response = await self.client.get("/api/v1/config")
        self.assertEqual(response.status_code, 200)
        for private in (TOKEN, "telegram", "postgres", "inverter_ip", "logger_serial", "schedule_street"):
            self.assertNotIn(private, response.text)


class ReadOnlyRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_repository_uses_readonly_transactions_bound_parameters_and_safe_event_allowlist(self):
        queries = []
        connection = SimpleNamespace(
            fetch=AsyncMock(side_effect=lambda query, *args, **kwargs: queries.append((query, args)) or [])
        )

        @asynccontextmanager
        async def transaction(**kwargs):
            self.assertEqual(kwargs, {"readonly": True})
            yield

        @asynccontextmanager
        async def acquire():
            yield connection

        connection.transaction = transaction
        pool = SimpleNamespace(acquire=acquire)
        now = datetime.now(UTC)
        await repository.fetch_history(pool, now - timedelta(hours=1), now, None, 2)
        await repository.fetch_aggregate(pool, now - timedelta(hours=1), now, 3600)
        await repository.fetch_events(pool, None, None, 10, 2)
        self.assertEqual(queries[0][1][-1], 3)
        self.assertIn("ORDER BY observed_at LIMIT $4", queries[0][0])
        self.assertNotIn("statistics", queries[2][1][0])
        self.assertNotIn("report_payload", queries[2][0])
        self.assertNotIn("forecast_text", queries[2][0])
        self.assertTrue(
            all("SELECT" in query and "INSERT" not in query and "UPDATE" not in query for query, _ in queries)
        )
