import asyncio
import unittest
from datetime import datetime, timezone

from deye_notifier.database.telemetry import fetch_samples, record_sample


class _Connection:
    def __init__(self):
        self.execute_call = None
        self.fetch_call = None

    async def execute(self, query, *args):
        self.execute_call = (query, args)

    async def fetch(self, query, *args):
        self.fetch_call = (query, args)
        return []


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self):
        self.connection = _Connection()

    def acquire(self):
        return _Acquire(self.connection)


class TelemetryEnergyCounterTests(unittest.TestCase):
    def test_record_sample_persists_optional_counter(self):
        pool = _Pool()
        observed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        asyncio.run(record_sample(pool, observed_at, True, 123, 80, total_load_energy_kwh=12.5))
        query, args = pool.connection.execute_call
        self.assertIn("total_load_energy_kwh", query)
        self.assertEqual(args[4], 12.5)

    def test_record_sample_persists_complete_optional_reading(self):
        pool = _Pool()
        observed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        asyncio.run(
            record_sample(
                pool,
                observed_at,
                True,
                123,
                80,
                grid_voltage=231.5,
                grid_connected=False,
                battery_current=-7.2,
                battery_capacity_ah=200,
                shutdown_soc=20,
                battery_power_watts=-400,
                battery_voltage=53.8,
                battery_temperature_c=22.4,
                inverter_dc_temperature_c=49,
                inverter_radiator_temperature_c=41.5,
                runtime_eta_seconds=7200,
                charge_eta_seconds=3600,
                battery_cutoff_soc=15,
            )
        )
        query, args = pool.connection.execute_call
        self.assertIn("inverter_radiator_temperature_c", query)
        self.assertEqual(len(args), 18)
        self.assertEqual(
            args[5:],
            (231.5, False, -7.2, 200.0, 20.0, -400, 53.8, 22.4, 49.0, 41.5, 7200.0, 3600.0, 15.0),
        )

    def test_record_sample_rejects_invalid_optional_reading_before_database_write(self):
        pool = _Pool()
        with self.assertRaisesRegex(ValueError, "grid_voltage"):
            asyncio.run(
                record_sample(
                    pool,
                    datetime(2025, 1, 1, tzinfo=timezone.utc),
                    True,
                    123,
                    80,
                    grid_voltage=float("nan"),
                )
            )
        self.assertIsNone(pool.connection.execute_call)

    def test_fetch_samples_selects_counter_for_all_boundary_sets(self):
        pool = _Pool()
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 1, 2, tzinfo=timezone.utc)
        asyncio.run(fetch_samples(pool, start, end))
        query, _args = pool.connection.fetch_call
        self.assertEqual(
            query.count("SELECT observed_at, is_grid_online, load_watts, battery_soc, total_load_energy_kwh"),
            6,
        )


if __name__ == "__main__":
    unittest.main()
