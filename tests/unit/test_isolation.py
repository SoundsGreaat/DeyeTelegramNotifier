"""Regression checks that keep test runs independent of operator settings."""

import os
import socket
import unittest
from unittest.mock import patch

from support import IsolatedTestConfig, isolated_config, local_test_database_url

from deye_notifier.database import migrations as db_migrations


class TestIsolationTests(IsolatedTestConfig, unittest.TestCase):
    def test_external_dns_and_ip_connections_are_blocked(self):
        with self.assertRaisesRegex(AssertionError, "outside loopback"):
            socket.getaddrinfo("api.telegram.org", 443)
        with socket.socket() as connection:
            with self.assertRaisesRegex(AssertionError, "outside loopback"):
                connection.connect(("192.0.2.1", 8899))
            with self.assertRaisesRegex(AssertionError, "outside loopback"):
                connection.connect_ex(("192.0.2.1", 8899))

    def test_operator_environment_does_not_affect_configuration(self):
        with (
            patch.dict(
                os.environ,
                {
                    "POSTGRES_HOST": "production.invalid",
                    "POSTGRES_PORT": "not-a-port",
                    "LEGACY_DB_TIMEZONE": "not-a-timezone",
                    "BATTERY_CUTOFF_SOC": "not-a-number",
                },
            ),
            isolated_config(),
        ):
            config = db_migrations.migration_config()
            url = config.attributes["database_url"]
            self.assertEqual(url.host, "127.0.0.1")
            self.assertEqual(url.port, 1)
            self.assertEqual(config.attributes["legacy_timezone"], "UTC")
            self.assertEqual(os.environ["BATTERY_CUTOFF_SOC"], "20")

    def test_integration_database_must_be_local_and_explicitly_named(self):
        for dsn in (
            "postgresql://test:test@production.invalid/deye_test",
            "postgresql://test:test@127.0.0.1/deye_bot",
            "postgresql://test:test@127.0.0.1/deye_test?host=production.invalid",
        ):
            with (
                self.subTest(dsn=dsn),
                self.assertRaisesRegex(ValueError, "loopback database named deye_test"),
            ):
                local_test_database_url(dsn)
        self.assertEqual(
            local_test_database_url("postgresql://test:test@127.0.0.1:5433/deye_test").port, 5433
        )
