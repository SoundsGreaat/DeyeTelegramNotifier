import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from deye_notifier.bot import locales
from deye_notifier.bot.status import render_status


def sample_reading(**overrides):
    fields = {
        "grid_voltage": 230.0,
        "battery_soc": 80,
        "load_watts": 1000,
        "battery_current": 12.3,
        "battery_capacity_ah": 200,
        "shutdown_soc": 20,
        "total_load_energy_kwh": 1234.5,
        "battery_power_watts": 500,
        "battery_voltage": 52.4,
        "battery_temperature_c": 21.0,
        "inverter_dc_temperature_c": 50.6,
        "inverter_radiator_temperature_c": 44.1,
        "charge_power_watts": None,
        "is_grid_online": False,
        "is_charging": False,
        "is_discharging": True,
    }
    fields.update(overrides)
    reading = SimpleNamespace(**fields)
    reading.cutoff_soc = lambda settings: (
        fields["shutdown_soc"] if fields["shutdown_soc"] is not None else settings.battery_cutoff_soc
    )
    reading.runtime_seconds = lambda settings: 43_200
    return reading


def sample_settings():
    return SimpleNamespace(
        stats_max_gap_seconds=60,
        stats_timezone=ZoneInfo("Europe/Kyiv"),
        battery_capacity_kwh=10,
        battery_cutoff_soc=20,
        battery_inverter_efficiency=0.9,
        has_meaningful_load=lambda watts: watts >= 10,
    )


class StatusFormatTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 1, 12, tzinfo=UTC)
        self.settings = sample_settings()
        self.kwargs = {
            "state_since": self.now - timedelta(hours=2),
            "charge_estimate": SimpleNamespace(is_charging=False, seconds_remaining=None),
            "now": self.now,
        }

    def test_compact_status_retains_existing_template_and_gates(self):
        reading = sample_reading(
            battery_soc=100,
            load_watts=5,
            battery_power_watts=-108,
            charge_power_watts=108,
            is_charging=True,
            is_discharging=False,
        )
        actual = render_status("en", reading, self.settings, **self.kwargs)
        expected = locales.get_message(
            "en",
            "status",
            grid_status=locales.get_message("en", "offline"),
            voltage=230,
            voltage_line="",
            battery=100,
            load=5,
            state_duration_line=locales.get_state_duration_line("en", False, 7200),
            runtime_line="",
            charge_power_line=locales.get_charge_power_line("en", 108),
            charge_time_line="",
            forecast_line="",
        )
        self.assertEqual(actual, expected)
        self.assertNotIn("Estimated runtime", actual)
        self.assertNotIn("°C", actual)

    def test_detailed_status_includes_available_measurements_in_order(self):
        text = render_status(
            "en",
            sample_reading(),
            self.settings,
            detailed=True,
            observed_at=self.now,
            **self.kwargs,
        )
        for value in (
            "Battery voltage: 52.4 V",
            "DC transformer temperature: 50.6 °C",
            "Inverter radiator temperature: 44.1 °C",
            "Battery temperature: 21.0 °C",
            "Battery current: 12.3 A",
            "Battery capacity: 200 Ah",
            "Shutdown cutoff: 20%",
            "DC discharge: 500 W",
            "Total load energy: 1234.5 kWh",
            "Estimated runtime",
            "Last reading: 01.11.2026 14:00:00 EET",
        ):
            self.assertIn(value, text)
        self.assertLess(text.index("Load: 1000 W"), text.index("Battery: 80%"))
        self.assertLess(text.index("Load: 1000 W"), text.index("DC transformer temperature:"))
        self.assertLess(text.index("Inverter radiator temperature:"), text.index("Battery: 80%"))
        self.assertLess(text.index("Battery: 80%"), text.index("Battery temperature:"))
        self.assertLess(text.index("Battery temperature:"), text.index("Battery voltage:"))
        self.assertLess(text.index("Battery: 80%"), text.index("Battery voltage:"))
        self.assertLess(text.index("Battery voltage:"), text.index("DC discharge:"))
        self.assertLess(text.index("DC discharge:"), text.index("Estimated runtime"))
        self.assertLess(text.index("Estimated runtime"), text.index("Last reading:"))

    def test_optional_measurements_are_omitted_when_unavailable(self):
        text = render_status(
            "en",
            sample_reading(
                battery_voltage=None,
                battery_temperature_c=None,
                inverter_dc_temperature_c=None,
                inverter_radiator_temperature_c=None,
                battery_current=None,
                battery_capacity_ah=None,
                shutdown_soc=None,
                total_load_energy_kwh=None,
                battery_power_watts=None,
                is_discharging=False,
            ),
            self.settings,
            detailed=True,
            observed_at=None,
            **self.kwargs,
        )
        for label in (
            "Battery voltage:",
            "Battery current:",
            "Battery capacity:",
            "DC discharge:",
            "Total load energy:",
            "Last reading:",
        ):
            self.assertNotIn(label, text)
        self.assertIn("Shutdown cutoff: 20%", text)
        self.assertNotIn("°C", text)

    def test_invalid_temperatures_are_omitted_without_hiding_zero_or_negative_values(self):
        for value in (None, float("nan"), float("inf"), -100, 5839.5):
            with self.subTest(value=value):
                text = render_status(
                    "en",
                    sample_reading(
                        battery_temperature_c=value,
                        inverter_dc_temperature_c=value,
                        inverter_radiator_temperature_c=value,
                    ),
                    self.settings,
                    detailed=True,
                    **self.kwargs,
                )
                self.assertNotIn("°C", text)
        text = render_status(
            "en",
            sample_reading(battery_temperature_c=0, inverter_dc_temperature_c=-5),
            self.settings,
            detailed=True,
            **self.kwargs,
        )
        self.assertIn("Battery temperature: 0.0 °C", text)
        self.assertIn("DC transformer temperature: -5.0 °C", text)

    def test_stale_detailed_status_is_marked_and_hides_predictions(self):
        for language, marker in (
            ("en", "Last known state"),
            ("uk", "Останній відомий стан"),
            ("es", "Último estado conocido"),
            ("de", "Zuletzt bekannter Zustand"),
        ):
            with self.subTest(language=language):
                text = render_status(
                    language,
                    sample_reading(),
                    self.settings,
                    detailed=True,
                    observed_at=self.now - timedelta(minutes=2),
                    forecast_action="turn_on",
                    forecast_text="<b>12:30 & soon</b>",
                    **self.kwargs,
                )
                self.assertIn(marker, text)
                self.assertIn("&lt;b&gt;12:30 &amp; soon&lt;/b&gt;", text)
                self.assertNotIn("Estimated runtime", text)
                self.assertNotIn("Usable reserve", text)
                self.assertNotIn("To 100%", text)
                self.assertIn(
                    "Last reading:"
                    if language == "en"
                    else {"uk": "Останнє зчитування:", "es": "Última lectura:", "de": "Letzte Messung:"}[
                        language
                    ],
                    text,
                )

    def test_detailed_labels_exist_for_every_supported_language(self):
        readings = sample_reading(
            load_watts=5,
            battery_soc=100,
            battery_power_watts=-108,
            charge_power_watts=108,
            is_charging=True,
            is_discharging=False,
        )
        for language in locales.LANGUAGES:
            with self.subTest(language=language):
                text = render_status(
                    language, readings, self.settings, detailed=True, observed_at=self.now, **self.kwargs
                )
                self.assertIn("52.4", text)
                self.assertIn("108", text)
                self.assertEqual(text.count("°C"), 3)
                if language != "en":
                    self.assertNotIn("Battery temperature:", text)
                    self.assertNotIn("Inverter radiator temperature:", text)
                self.assertNotIn("Estimated runtime", text)


if __name__ == "__main__":
    unittest.main()
