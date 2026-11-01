import unittest
from dataclasses import replace
from unittest.mock import Mock

from support import IsolatedTestConfig

from deye_notifier.services.schedules import YasnoScheduleProvider


class ScheduleProviderTests(IsolatedTestConfig, unittest.TestCase):
    def test_provider_uses_configured_ids_in_request_path(self):
        settings = replace(
            self.settings,
            schedule_provider="yasno",
            schedule_region_id="25",
            schedule_dso_id="902",
            schedule_street="Example",
            schedule_house="10",
        )
        provider = YasnoScheduleProvider(None, settings)
        provider._resolve_group = Mock(return_value="1.1")
        provider._get_json = Mock(return_value={"1.1": {"today": {"slots": []}}})
        payload = provider._fetch_schedule()
        self.assertTrue(provider.enabled)
        self.assertEqual(payload["group"], "1.1")
        provider._get_json.assert_called_once_with("/regions/25/dsos/902/planned-outages", {})
