"""Offline behavior checks: no inverter, Telegram or database connection."""

import json
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch
from zoneinfo import ZoneInfo

from support import IsolatedTestConfig, isolated_config

with isolated_config():
    import main

import locales
from aiogram.filters import CommandObject
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SetMyCommands


def make_stats():
    return {
        'energy_kwh': 3.5, 'online_seconds': 3600, 'offline_seconds': 1800,
        'unknown_seconds': 600, 'coverage_percent': 90,
        'outages': 1, 'longest_outage_seconds': 1800, 'avg_load_watts': 500,
        'peak_load_watts': 1500, 'min_soc': 25, 'max_soc': 100,
    }


class BatteryTests(IsolatedTestConfig, unittest.TestCase):
    def test_charging_crosses_all_milestones_and_full(self):
        self.assertEqual(main.DeyeStatusMonitor.battery_events(20, 100), [
            ('battery_charging', 25), ('battery_charging', 50),
            ('battery_charging', 75), ('battery_full', 100),
        ])

    def test_discharge_jump_does_not_skip_low_alert(self):
        self.assertEqual(main.DeyeStatusMonitor.battery_events(95, 20), [
            ('battery_discharging', 75), ('battery_discharging', 50), ('battery_low', 25),
        ])
        self.assertEqual(main.DeyeStatusMonitor.battery_events(None, 20), [])
        self.assertEqual(main.DeyeStatusMonitor.battery_events(50, 50), [])

    def test_runtime_accounts_for_reserve_and_efficiency(self):
        for language in locales.LANGUAGES:
            line = locales.get_runtime_line(language, 80, 1000, 10, 20, .9)
            self.assertIn(locales.format_duration(5.4 * 3600, language), line)
            self.assertIn('5.40', line)
            self.assertIn('0', locales.get_runtime_line(language, 15, 1000, 10, 20, .9))
            self.assertIn('BATTERY_CAPACITY_KWH', locales.get_runtime_line(language, 80, 1000, 0, 20, .9))
            self.assertNotIn('inf', locales.get_runtime_line(language, 80, 0, 10, 20, .9))

    def test_reports_render_in_every_language(self):
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        for language in locales.LANGUAGES:
            for period in ('day', 'week', 'month'):
                report = locales.format_statistics(language, period, start, start + timedelta(hours=2), make_stats())
                self.assertIn('3.50', report)
                self.assertIn('25–100%', report)
                self.assertNotIn('{', report)
                self.assertLess(len(report), 4096)
            self.assertEqual(locales.format_statistics(language, 'day', start, start, {}),
                             locales.get_message(language, 'stats_empty'))

    def test_dst_report_does_not_invent_unknown_hours(self):
        tz = ZoneInfo('Europe/Berlin')
        for year, month, day, expected_hours in ((2025, 3, 30, 23), (2025, 10, 26, 25)):
            start = datetime(year, month, day, tzinfo=tz)
            end = start + timedelta(days=1)
            stats = make_stats()
            stats.update(online_seconds=expected_hours * 3600, offline_seconds=0,
                         unknown_seconds=0, coverage_percent=100)
            report = locales.format_statistics('en', 'day', start, end, stats)
            self.assertIn(f'Grid available: {expected_hours:.1f} h (100%)', report)
            self.assertIn('Unknown gaps: 0.0 h (0%)', report)


class MonitorTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    async def test_saved_state_duration_survives_restart_and_charging_is_queued(self):
        monitor = main.DeyeStatusMonitor(Mock(), SimpleNamespace(next_event_for_state=lambda _: ('', '')))
        since = datetime.now(timezone.utc) - timedelta(hours=3)
        monitor.read_inverter = Mock(return_value=(230, 0, 24, 1000))
        monitor.broadcast = AsyncMock()
        with patch.object(main, 'load_monitor_state', AsyncMock(return_value={
            'is_grid_online': True, 'last_status_time': since,
        })), patch.object(main.energy_stats, 'record_sample', AsyncMock()) as record, \
                patch.object(main, 'record_transition', AsyncMock()) as transition:
            await monitor.check()
            self.assertEqual(monitor.last_status_time, since)
            monitor.broadcast.assert_not_awaited()
            monitor.read_inverter.return_value = (230, 0, 51, 1000)
            await monitor.check()
            self.assertEqual(monitor.last_status_time, since)
            transition.assert_not_awaited()
            self.assertEqual(record.await_count, 2)
            self.assertEqual([call.kwargs['threshold'] for call in monitor.broadcast.await_args_list], [50])

    async def test_soc_jump_sends_one_current_level_notification(self):
        monitor = main.DeyeStatusMonitor(None, None)
        monitor.is_grid_online = True
        monitor.last_battery_soc = 20
        monitor.read_inverter = Mock(return_value=(230, 0, 100, 1000))
        monitor.broadcast = AsyncMock()
        with patch.object(main.energy_stats, 'record_sample', AsyncMock()):
            await monitor.check()
        monitor.broadcast.assert_awaited_once_with('battery_full', level=100, threshold=100)

    async def test_transition_is_persisted_before_new_duration(self):
        monitor = main.DeyeStatusMonitor(Mock(), SimpleNamespace(next_event_for_state=lambda _: ('', '')))
        monitor.is_grid_online = True
        monitor.last_battery_soc = 60
        monitor.last_status_time = datetime.now(timezone.utc) - timedelta(hours=1)
        monitor.read_inverter = Mock(return_value=(0, 300, 60, 600))
        with patch.object(main.energy_stats, 'record_sample', AsyncMock()), \
                patch.object(main, 'record_transition', AsyncMock()) as transition:
            result = await monitor.check()
            self.assertEqual(result, (0, 300, 60, 600))
            self.assertFalse(monitor.is_grid_online)
            self.assertEqual(monitor.last_status_time, transition.await_args.args[2])

    async def test_status_uses_shared_monitor_and_displays_duration_and_runtime(self):
        wait = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(chat=SimpleNamespace(id=123), answer=AsyncMock(return_value=wait))
        monitor = SimpleNamespace(check=AsyncMock(return_value=(230, 0, 80, 1000)),
                                  last_status_time=datetime.now(timezone.utc) - timedelta(hours=2))
        provider = SimpleNamespace(next_event_for_state=lambda _: ('', ''))
        with patch.object(main, 'get_chat_language', AsyncMock(return_value='uk')), \
                patch.object(main, 'BATTERY_CAPACITY_KWH', 10), \
                patch.object(main, 'BATTERY_CUTOFF_SOC', 20), \
                patch.object(main, 'BATTERY_INVERTER_EFFICIENCY', .9):
            await main.cmd_status(message, None, provider, monitor)
        text = wait.edit_text.await_args.args[0]
        self.assertIn('Світло є вже: 2г', text)
        self.assertIn('5г 24хв', text)
        monitor.check.assert_awaited_once()

    async def test_invalid_stats_argument_gets_usage(self):
        message = SimpleNamespace(chat=SimpleNamespace(id=123), answer=AsyncMock())
        with patch.object(main, 'get_chat_language', AsyncMock(return_value='uk')):
            await main.cmd_stats(message, None, CommandObject(command='stats', args='year'))
        message.answer.assert_awaited_once_with(locales.get_message('uk', 'stats_usage'))


class SubscriptionCommandTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    def message(self):
        return SimpleNamespace(chat=SimpleNamespace(id=123),
                               from_user=SimpleNamespace(language_code='uk'), answer=AsyncMock())

    async def test_stats_commands_save_explicit_choice_and_respect_saved_language(self):
        for command, enabled, key in (
            ('stats_auto_on', True, 'stats_auto_enabled'),
            ('stats_auto_off', False, 'stats_auto_disabled'),
        ):
            message = self.message()
            with patch.object(main, 'set_stats_subscription', AsyncMock(return_value='de')) as save:
                await main.cmd_stats_auto(message, None, CommandObject(command=command))
            save.assert_awaited_once_with(None, 123, enabled, 'uk')
            message.answer.assert_awaited_once_with(locales.get_message('de', key))

    async def test_global_pause_is_explained_when_saving_subscription(self):
        message = self.message()
        with patch.object(main, 'set_stats_subscription', AsyncMock(return_value='uk')), \
                patch.object(main, 'STATS_AUTO_REPORTS', False):
            await main.cmd_stats_auto(message, None, CommandObject(command='stats_auto_on'))
        self.assertIn(locales.get_message('uk', 'stats_auto_paused'), message.answer.await_args.args[0])

    async def test_start_only_enables_power_notifications(self):
        message = self.message()
        with patch.object(main, 'add_chat', AsyncMock()) as add, \
                patch.object(main, 'set_stats_subscription', AsyncMock()) as stats:
            await main.cmd_start(message, None)
        add.assert_awaited_once_with(None, 123, 'uk')
        stats.assert_not_awaited()

    async def test_stop_turns_off_all_subscriptions(self):
        message = self.message()
        with patch.object(main, 'unsubscribe_chat', AsyncMock()) as unsubscribe:
            await main.cmd_stop(message, None)
        unsubscribe.assert_awaited_once_with(None, 123)


class CommandRegistrationTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    async def test_menu_is_english_and_has_week_and_month_shortcuts(self):
        with patch.object(main.bot, 'set_my_commands', AsyncMock()) as register, \
                patch.object(main.bot, 'delete_my_commands', AsyncMock()) as delete:
            await main.register_bot_commands()
        self.assertEqual({item.kwargs['language_code'] for item in register.await_args_list},
                         {'', *locales.LANGUAGES})
        for item in register.await_args_list:
            self.assertEqual(item.kwargs['scope'].type, 'default')
            commands = {command.command: command.description for command in item.args[0]}
            self.assertEqual(commands['stats_week'], "This week's electricity statistics")
            self.assertEqual(commands['stats_month'], "This month's electricity statistics")
            self.assertTrue(all(description.isascii() for description in commands.values()))
        delete.assert_awaited_once_with(scope=main.types.BotCommandScopeDefault(), language_code='ru', request_timeout=10)

    async def test_transient_api_failure_is_retried(self):
        error = TelegramNetworkError(method=SetMyCommands(commands=[]), message='synthetic failure')
        with patch.object(main.bot, 'set_my_commands', AsyncMock(side_effect=[error] + [True] * (len(locales.LANGUAGES) + 1))) as register, \
                patch.object(main.bot, 'delete_my_commands', AsyncMock()), \
                patch.object(main.asyncio, 'sleep', AsyncMock()) as sleep:
            await main.register_bot_commands()
        self.assertEqual(register.await_count, 1 + len(locales.LANGUAGES) + 1)
        sleep.assert_awaited_once_with(1)

    async def test_api_outage_does_not_abort_startup(self):
        error = TelegramNetworkError(method=SetMyCommands(commands=[]), message='synthetic failure')
        with patch.object(main.bot, 'set_my_commands', AsyncMock(side_effect=error)) as register, \
                patch.object(main.asyncio, 'sleep', AsyncMock()) as sleep:
            await main.register_bot_commands()
        self.assertEqual(register.await_count, 3)
        self.assertEqual(sleep.await_args_list, [call(1), call(2)])

    async def test_startup_registers_menu_after_migrations_before_polling(self):
        order = []

        async def migrate():
            order.append('migrate')

        async def register():
            order.append('register')

        async def poll(*args, **kwargs):
            order.append('poll')

        monitor = SimpleNamespace(run=AsyncMock(), delivery_loop=AsyncMock(), running=True)
        provider = SimpleNamespace(run=AsyncMock())
        pool = SimpleNamespace(close=AsyncMock())
        with patch.object(main, 'get_db_pool', AsyncMock(return_value=pool)), \
                patch.object(main, 'prepare_database', migrate), \
                patch.object(main, 'register_bot_commands', register), \
                patch.object(main, 'DeyeStatusMonitor', return_value=monitor), \
                patch.object(main, 'YasnoScheduleProvider', return_value=provider), \
                patch.object(main, 'statistics_loop', AsyncMock()), \
                patch.object(main.dp, 'start_polling', poll):
            await main.main()
        self.assertEqual(order, ['migrate', 'register', 'poll'])
        pool.close.assert_awaited_once()


class ReportQueueTests(IsolatedTestConfig, unittest.IsolatedAsyncioTestCase):
    async def test_period_keys_survive_repeat_scheduler_and_respect_local_hour(self):
        queued = {}

        class Pool:
            @asynccontextmanager
            async def acquire(self):
                yield self

            async def fetchval(self, _query, key):
                return key in queued

            async def execute(self, _query, key, payload):
                queued.setdefault(key, json.loads(payload))

        pool = Pool()
        tz = ZoneInfo('Europe/Kyiv')
        with patch.object(main, 'STATS_TIMEZONE', tz), patch.object(main, 'STATS_REPORT_HOUR', 9), \
                patch.object(main, 'query_statistics', AsyncMock(return_value=make_stats())) as query:
            await main.enqueue_completed_reports(pool, datetime(2026, 6, 1, 8, 59, tzinfo=tz))
            before = [value for value in queued.values() if value['period'] == 'day'][0]
            self.assertIn('2026-05-30', before['start'])
            queued.clear()
            await main.enqueue_completed_reports(pool, datetime(2026, 6, 1, 9, tzinfo=tz))
            self.assertEqual(len(queued), 3)
            periods = {value['period']: value for value in queued.values()}
            self.assertIn('2026-05-31', periods['day']['start'])
            self.assertIn('2026-05-25', periods['week']['start'])
            self.assertIn('2026-05-01', periods['month']['start'])
            calls = query.await_count
            await main.enqueue_completed_reports(pool, datetime(2026, 6, 1, 10, tzinfo=tz))
            self.assertEqual(query.await_count, calls)
            self.assertEqual(len(queued), 3)

    async def test_report_delivery_formats_saved_payload_for_chat_language(self):
        monitor = main.DeyeStatusMonitor(None, None)
        event = dict(id=1, message_key='statistics', voltage=None, load_watts=0,
                     duration='', level=None, threshold=None, forecast_action=None, forecast_text=None,
                     report_payload=json.dumps({'period': 'day', 'start': '2026-11-01T00:00:00+02:00',
                                                'end': '2026-11-02T00:00:00+02:00', 'stats': make_stats()}))
        with patch.object(main, 'get_pending_notifications', AsyncMock(return_value=[event])), \
                patch.object(main, 'get_chats', AsyncMock(return_value=[(123, 'uk')])) as recipients, \
                patch.object(main.bot, 'send_message', AsyncMock()) as send, \
                patch.object(main, 'mark_notification_delivered', AsyncMock()) as mark:
            await monitor.deliver_pending()
        self.assertIn('Денна', send.await_args.args[1])
        self.assertIn('3.50', send.await_args.args[1])
        recipients.assert_awaited_once_with(None, stats_only=True)
        mark.assert_awaited_once_with(None, 1)

    async def test_report_without_opted_in_recipients_is_not_sent(self):
        monitor = main.DeyeStatusMonitor(None, None)
        with patch.object(main, 'get_pending_notifications', AsyncMock(return_value=[
            {'id': 1, 'message_key': 'statistics'},
        ])), patch.object(main, 'get_chats', AsyncMock(return_value=[])) as recipients, \
                patch.object(main.bot, 'send_message', AsyncMock()) as send, \
                patch.object(main, 'mark_notification_delivered', AsyncMock()) as mark:
            await monitor.deliver_pending()
        recipients.assert_awaited_once_with(None, stats_only=True)
        send.assert_not_awaited()
        mark.assert_awaited_once_with(None, 1)

    async def test_power_alerts_use_power_subscribers(self):
        monitor = main.DeyeStatusMonitor(None, None)
        event = dict(id=1, message_key='battery_full', voltage=None, load_watts=0,
                     duration='', level=100, threshold=100, forecast_action=None,
                     forecast_text=None, report_payload=None)
        with patch.object(main, 'get_pending_notifications', AsyncMock(return_value=[event])), \
                patch.object(main, 'get_chats', AsyncMock(return_value=[(123, 'uk')])) as recipients, \
                patch.object(main.bot, 'send_message', AsyncMock()) as send, \
                patch.object(main, 'mark_notification_delivered', AsyncMock()):
            await monitor.deliver_pending()
        recipients.assert_awaited_once_with(None, stats_only=False)
        self.assertEqual(send.await_args.args[0], 123)

    async def test_disabled_global_reports_do_not_deliver_old_queue_events(self):
        monitor = main.DeyeStatusMonitor(None, None)
        with patch.object(main, 'get_pending_notifications', AsyncMock(return_value=[
            {'id': 1, 'message_key': 'statistics'},
        ])), patch.object(main, 'STATS_AUTO_REPORTS', False), \
                patch.object(main.bot, 'send_message', AsyncMock()) as send, \
                patch.object(main, 'mark_notification_delivered', AsyncMock()) as mark:
            await monitor.deliver_pending()
        send.assert_not_awaited()
        mark.assert_awaited_once_with(None, 1)


if __name__ == '__main__':
    unittest.main()
