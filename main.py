import os
import asyncio
import json
import logging
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from pysolarmanv5 import PySolarmanV5
from dotenv import load_dotenv
import locales

load_dotenv()

INVERTER_IP = os.getenv('INVERTER_IP', '192.168.1.151')
try:
    LOGGER_SERIAL = int(os.getenv('LOGGER_SERIAL', '0'))
except ValueError:
    print("Error: LOGGER_SERIAL must be an integer")
    sys.exit(1)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '10'))
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

PG_HOST = os.getenv('POSTGRES_HOST', 'postgres')
PG_USER = os.getenv('POSTGRES_USER', 'postgres')
PG_PASS = os.getenv('POSTGRES_PASSWORD', 'postgres')
PG_DB = os.getenv('POSTGRES_DB', 'deye_bot')

# Optional schedule forecasts. Leave SCHEDULE_PROVIDER empty to keep the bot
# fully provider-agnostic and disable address lookups.
SCHEDULE_PROVIDER = os.getenv('SCHEDULE_PROVIDER', '').strip().lower()
SCHEDULE_REGION_ID = os.getenv('SCHEDULE_REGION_ID', '').strip()
SCHEDULE_DSO_ID = os.getenv('SCHEDULE_DSO_ID', '').strip()
SCHEDULE_CITY_ID = os.getenv('SCHEDULE_CITY_ID', '').strip()
SCHEDULE_STREET = os.getenv('SCHEDULE_STREET', '').strip()
SCHEDULE_HOUSE = os.getenv('SCHEDULE_HOUSE', '').strip()
SCHEDULE_TIMEZONE = os.getenv('SCHEDULE_TIMEZONE', 'UTC').strip()
SCHEDULE_REFRESH_INTERVAL = max(60, int(os.getenv('SCHEDULE_REFRESH_INTERVAL', '600')))
SCHEDULE_REQUEST_TIMEOUT = max(1.0, float(os.getenv('SCHEDULE_REQUEST_TIMEOUT', '4')))

GRID_V_REG = 150
MODE_REG = 164
VOLTAGE_THRESHOLD = 100

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN is required")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


async def get_db_pool():
    for i in range(10):
        try:
            return await asyncpg.create_pool(
                user=PG_USER,
                password=PG_PASS,
                database=PG_DB,
                host=PG_HOST,
                min_size=1,
                max_size=5,
                max_inactive_connection_lifetime=300,
            )
        except Exception as e:
            logger.warning(f"Waiting for database... ({e})")
            await asyncio.sleep(2)
    raise Exception("Could not connect to database")


async def db_retry(pool, operation, attempts=3):
    """Retry transient/stale PostgreSQL connections instead of killing the monitor task."""
    for attempt in range(attempts):
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception:
            if attempt >= attempts - 1:
                raise
            try:
                await pool.expire_connections()
            except Exception:
                pass
            await asyncio.sleep(1 + attempt)


async def init_db(pool):
    async with pool.acquire() as conn:
        await conn.execute('''
                           CREATE TABLE IF NOT EXISTS chats
                           (
                               chat_id    BIGINT PRIMARY KEY,
                               created_at TIMESTAMP  DEFAULT CURRENT_TIMESTAMP,
                               language   VARCHAR(5) DEFAULT 'en'
                           )
                           ''')
        try:
            await conn.execute('ALTER TABLE chats ADD COLUMN IF NOT EXISTS language VARCHAR(5) DEFAULT \'en\'')
        except Exception:
            pass
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS monitor_state (
                id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id = TRUE),
                is_grid_online BOOLEAN NOT NULL,
                last_status_time TIMESTAMP NOT NULL
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS notification_queue (
                id BIGSERIAL PRIMARY KEY,
                message_key VARCHAR(64) NOT NULL,
                voltage DOUBLE PRECISION,
                load_watts INTEGER DEFAULT 0,
                duration VARCHAR(64) DEFAULT '',
                level INTEGER,
                forecast_action VARCHAR(16),
                forecast_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                delivered_at TIMESTAMP NULL
            )
        ''')
        await conn.execute('ALTER TABLE notification_queue ADD COLUMN IF NOT EXISTS forecast_action VARCHAR(16)')
        await conn.execute('ALTER TABLE notification_queue ADD COLUMN IF NOT EXISTS forecast_text TEXT')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS schedule_cache (
                cache_key VARCHAR(128) PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')


async def add_chat(pool, chat_id, language='en'):
    async with pool.acquire() as conn:
        await conn.execute('''
                           INSERT INTO chats (chat_id, language)
                           VALUES ($1, $2)
                           ON CONFLICT (chat_id) DO UPDATE SET language = $2
                           ''', chat_id, language)


async def set_chat_language(pool, chat_id, language):
    async with pool.acquire() as conn:
        await conn.execute('UPDATE chats SET language = $1 WHERE chat_id = $2', language, chat_id)


async def remove_chat(pool, chat_id):
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM chats WHERE chat_id = $1', chat_id)


async def get_chats(pool):
    async def operation():
        async with pool.acquire() as conn:
            rows = await conn.fetch('SELECT chat_id, language FROM chats')
            return [(row['chat_id'], row['language']) for row in rows]
    return await db_retry(pool, operation)


async def load_monitor_state(pool):
    async def operation():
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                'SELECT is_grid_online, last_status_time FROM monitor_state WHERE id = TRUE'
            )
    return await db_retry(pool, operation)


async def save_initial_monitor_state(pool, is_grid_online, status_time):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO monitor_state (id, is_grid_online, last_status_time)
                VALUES (TRUE, $1, $2)
                ON CONFLICT (id) DO UPDATE SET
                    is_grid_online = EXCLUDED.is_grid_online,
                    last_status_time = EXCLUDED.last_status_time
            ''', is_grid_online, status_time)
    await db_retry(pool, operation)


async def record_transition(pool, is_grid_online, status_time, voltage, load, duration,
                            forecast_action='', forecast_text=''):
    """Persist the state change and notification before attempting Telegram delivery."""
    async def operation():
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute('''
                    INSERT INTO notification_queue
                        (message_key, voltage, load_watts, duration, forecast_action, forecast_text)
                    VALUES ($1, $2, $3, $4, $5, $6)
                ''', 'power_restored' if is_grid_online else 'warning_battery',
                     voltage, load, duration, forecast_action, forecast_text)
                await conn.execute('''
                    INSERT INTO monitor_state (id, is_grid_online, last_status_time)
                    VALUES (TRUE, $1, $2)
                    ON CONFLICT (id) DO UPDATE SET
                        is_grid_online = EXCLUDED.is_grid_online,
                        last_status_time = EXCLUDED.last_status_time
                ''', is_grid_online, status_time)
    await db_retry(pool, operation)


async def enqueue_notification(pool, message_key, voltage=None, load=0, duration='', level=None,
                               forecast_action='', forecast_text=''):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO notification_queue
                    (message_key, voltage, load_watts, duration, level, forecast_action, forecast_text)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            ''', message_key, voltage, load, duration, level, forecast_action, forecast_text)
    await db_retry(pool, operation)


async def get_pending_notifications(pool):
    async def operation():
        async with pool.acquire() as conn:
            return await conn.fetch('''
                SELECT id, message_key, voltage, load_watts, duration, level,
                       forecast_action, forecast_text
                FROM notification_queue
                WHERE delivered_at IS NULL
                ORDER BY id
                LIMIT 20
            ''')
    return await db_retry(pool, operation)


async def mark_notification_delivered(pool, event_id):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute(
                'UPDATE notification_queue SET delivered_at = CURRENT_TIMESTAMP WHERE id = $1',
                event_id,
            )
    await db_retry(pool, operation)


async def load_schedule_cache(pool, cache_key):
    async def operation():
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT payload FROM schedule_cache WHERE cache_key = $1',
                cache_key,
            )
            return json.loads(row['payload']) if row else None
    return await db_retry(pool, operation)


async def save_schedule_cache(pool, cache_key, payload):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO schedule_cache (cache_key, payload, updated_at)
                VALUES ($1, $2::jsonb, CURRENT_TIMESTAMP)
                ON CONFLICT (cache_key) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
            ''', cache_key, json.dumps(payload, ensure_ascii=False))
    await db_retry(pool, operation)


class YasnoScheduleProvider:
    """Optional Kyiv/YASNO adapter; disabled unless all schedule settings are provided."""

    API_BASE = 'https://app.yasno.ua/api/blackout-service/public/shutdowns'
    OUTAGE_TYPES = {'Definite', 'Likely', 'Probable', 'Emergency'}

    def __init__(self, pool):
        self.pool = pool
        self.enabled = (
            SCHEDULE_PROVIDER == 'yasno'
            and bool(SCHEDULE_REGION_ID)
            and bool(SCHEDULE_DSO_ID)
            and bool(SCHEDULE_STREET)
            and bool(SCHEDULE_HOUSE)
        )
        self.cache_key = '|'.join([
            SCHEDULE_PROVIDER,
            SCHEDULE_REGION_ID,
            SCHEDULE_DSO_ID,
            SCHEDULE_CITY_ID,
            SCHEDULE_STREET.casefold(),
            SCHEDULE_HOUSE.casefold(),
        ])
        try:
            self.timezone = ZoneInfo(SCHEDULE_TIMEZONE)
        except Exception:
            logger.warning('Invalid SCHEDULE_TIMEZONE=%s; using local timezone', SCHEDULE_TIMEZONE)
            self.timezone = datetime.now().astimezone().tzinfo
        self.schedule = None
        self.group = None

    def _params(self, **kwargs):
        params = dict(kwargs)
        params['regionId'] = SCHEDULE_REGION_ID
        params['dsoId'] = SCHEDULE_DSO_ID
        if SCHEDULE_CITY_ID:
            params['cityId'] = SCHEDULE_CITY_ID
        return params

    def _get_json(self, path, params):
        url = f"{self.API_BASE}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                'Accept': 'application/json',
                'User-Agent': 'DeyeTelegramNotifier/1.0',
            },
        )
        with urllib.request.urlopen(request, timeout=SCHEDULE_REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode('utf-8'))

    @staticmethod
    def _select_value(items, wanted):
        if not items:
            return None
        wanted = wanted.casefold().strip()
        exact = next((item for item in items if str(item.get('value', '')).casefold().strip() == wanted), None)
        if exact:
            return exact
        return next((item for item in items if wanted in str(item.get('value', '')).casefold()), None)

    def _resolve_group(self):
        street_response = self._get_json('/addresses/v2/streets', self._params(query=SCHEDULE_STREET))
        street = self._select_value(street_response, SCHEDULE_STREET)
        if not street:
            raise RuntimeError(f"Street not found: {SCHEDULE_STREET}")

        house_response = self._get_json(
            '/addresses/v2/houses',
            self._params(streetId=street['id'], query=SCHEDULE_HOUSE),
        )
        house = self._select_value(house_response, SCHEDULE_HOUSE)
        if not house:
            raise RuntimeError(f"House not found: {SCHEDULE_HOUSE}")

        group_response = self._get_json(
            '/addresses/v2/group',
            self._params(streetId=street['id'], houseId=house['id']),
        )
        if isinstance(group_response, (str, int)):
            return str(group_response)
        group = group_response.get('group')
        subgroup = group_response.get('subgroup') or 0
        if group is None:
            raise RuntimeError(f"No schedule group for house: {SCHEDULE_HOUSE}")
        return f'{group}.{subgroup}' if subgroup else str(group)

    def _fetch_schedule(self):
        group = self._resolve_group()
        schedules = self._get_json(
            f'/regions/{SCHEDULE_REGION_ID}/dsos/{SCHEDULE_DSO_ID}/planned-outages',
            {},
        )
        group_schedule = schedules.get(group)
        if not group_schedule:
            raise RuntimeError(f"Schedule group {group} was not returned by provider")
        return {
            'group': group,
            'today': group_schedule.get('today'),
            'tomorrow': group_schedule.get('tomorrow'),
            'updated_on': group_schedule.get('updatedOn'),
        }

    async def load_cache(self):
        if not self.enabled:
            return
        try:
            self.schedule = await load_schedule_cache(self.pool, self.cache_key)
            if self.schedule:
                self.group = self.schedule.get('group')
        except Exception:
            logger.exception('Could not load schedule cache')

    async def refresh(self):
        if not self.enabled:
            return
        try:
            payload = await asyncio.to_thread(self._fetch_schedule)
            self.schedule = payload
            self.group = payload.get('group')
            await save_schedule_cache(self.pool, self.cache_key, payload)
            logger.info('Schedule updated: provider=yasno group=%s updated=%s', self.group, payload.get('updated_on'))
        except Exception as e:
            logger.warning('Schedule update failed: %s', e)

    async def run(self):
        if not self.enabled:
            logger.info('Schedule forecasts disabled: SCHEDULE_PROVIDER is not configured')
            return
        await self.load_cache()
        while True:
            await self.refresh()
            await asyncio.sleep(SCHEDULE_REFRESH_INTERVAL)

    @staticmethod
    def _day_start(day_payload, fallback_date, timezone):
        raw_date = (day_payload or {}).get('date')
        if raw_date:
            try:
                return datetime.fromisoformat(raw_date).astimezone(timezone).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            except ValueError:
                pass
        return datetime.combine(fallback_date, datetime.min.time(), tzinfo=timezone)

    @staticmethod
    def _format_range(start, end):
        if start.date() == end.date():
            return f'{start:%d.%m.%Y %H:%M}–{end:%H:%M}'
        return f'{start:%d.%m.%Y %H:%M}–{end:%d.%m.%Y %H:%M}'

    def next_event_for_state(self, is_grid_online):
        """Return (explicit action, local time) for the next scheduled transition.

        The inverter state remains authoritative; the schedule only describes what
        the provider expects to happen next.
        """
        if not self.enabled or not self.schedule:
            return '', ''
        now = datetime.now(self.timezone)
        for offset, key in enumerate(('today', 'tomorrow')):
            day_payload = self.schedule.get(key) or {}
            day_start = self._day_start(day_payload, now.date() + timedelta(days=offset), self.timezone)
            for slot in day_payload.get('slots', []):
                start = day_start + timedelta(minutes=int(slot['start']))
                end = day_start + timedelta(minutes=int(slot['end']))
                is_outage = slot.get('type') in self.OUTAGE_TYPES
                if is_grid_online and is_outage and start >= now:
                    return 'turn_off', self._format_range(start, end)
                if not is_grid_online and is_outage and start <= now < end:
                    return 'turn_on', f'{end:%d.%m.%Y %H:%M}'
        return '', ''


async def get_chat_language(pool, chat_id):
    async with pool.acquire() as conn:
        val = await conn.fetchval('SELECT language FROM chats WHERE chat_id = $1', chat_id)
        return val or 'en'


@dp.message(Command("start"))
async def cmd_start(message: types.Message, pool: asyncpg.Pool):
    user_lang = message.from_user.language_code if message.from_user.language_code in locales.LANGUAGES else 'en'
    await add_chat(pool, message.chat.id, user_lang)
    await message.answer(locales.get_message(user_lang, "start"))


@dp.message(Command("stop"))
async def cmd_stop(message: types.Message, pool: asyncpg.Pool):
    await remove_chat(pool, message.chat.id)
    user_lang = message.from_user.language_code if message.from_user.language_code in locales.LANGUAGES else 'en'
    await message.answer(locales.get_message(user_lang, "stop"))


@dp.message(Command("lang", "language"))
async def cmd_lang(message: types.Message):
    builder = InlineKeyboardBuilder()
    for code, name in locales.LANGUAGES.items():
        builder.button(text=name, callback_data=f"lang_{code}")
    builder.adjust(2)

    user_lang = message.from_user.language_code if message.from_user.language_code in locales.LANGUAGES else 'en'
    await message.answer(locales.get_message(user_lang, "select_lang"), reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("lang_"))
async def callback_lang(callback: types.CallbackQuery, pool: asyncpg.Pool):
    lang_code = callback.data.split("_")[1]
    if lang_code in locales.LANGUAGES:
        await set_chat_language(pool, callback.message.chat.id, lang_code)
        await callback.message.answer(locales.get_message(lang_code, "lang_set"))
        await callback.answer()
    else:
        await callback.answer("Invalid language")


@dp.message(Command("status"))
async def cmd_status(message: types.Message, pool: asyncpg.Pool, schedule_provider: YasnoScheduleProvider):
    user_lang = await get_chat_language(pool, message.chat.id)
    wait_msg = await message.answer("⏳")

    def read_inv():
        inv = None
        try:
            inv = PySolarmanV5(INVERTER_IP, LOGGER_SERIAL, socket_timeout=5, verbose=False)
            data = inv.read_holding_registers(GRID_V_REG, 35)
            return data[0] / 10.0, data[14], data[34], data[28]
        except Exception as e:
            logger.error(f"Manual check error: {e}")
            return None, None, None, None
        finally:
            if inv:
                try:
                    inv.disconnect()
                except:
                    pass

    loop = asyncio.get_running_loop()
    grid_v, current_mode, battery_soc, load_watts = await loop.run_in_executor(None, read_inv)

    if grid_v is None:
        await wait_msg.edit_text("❌ Connection failed")
        return

    is_online = (grid_v > VOLTAGE_THRESHOLD) and (current_mode != 300)
    grid_status_text = locales.get_message(user_lang, "online" if is_online else "offline")
    forecast_action, forecast_text = schedule_provider.next_event_for_state(is_online)
    forecast_line = locales.get_forecast_line(user_lang, forecast_action, forecast_text)

    text = locales.get_message(user_lang, "status",
                               grid_status=grid_status_text,
                               voltage=grid_v,
                               battery=battery_soc,
                               load=load_watts,
                               forecast_line=forecast_line)

    await wait_msg.edit_text(text, parse_mode=ParseMode.HTML)


class DeyeStatusMonitor:
    def __init__(self, pool, schedule_provider):
        self.pool = pool
        self.schedule_provider = schedule_provider
        self.is_grid_online = None
        self.last_battery_soc = None
        self.running = True
        self.last_status_time = datetime.now()

    def format_duration(self, delta):
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m {seconds}s"

    async def broadcast(self, key, **kwargs):
        await enqueue_notification(
            self.pool,
            key,
            voltage=kwargs.get('voltage'),
            load=kwargs.get('load', 0),
            duration=kwargs.get('duration', ''),
            level=kwargs.get('level'),
            forecast_text=kwargs.get('forecast_text', ''),
        )

    async def deliver_pending(self):
        """Deliver durable events; leave failed events queued for the next attempt."""
        events = await get_pending_notifications(self.pool)
        if not events:
            return

        chats = await get_chats(self.pool)
        if not chats:
            for event in events:
                await mark_notification_delivered(self.pool, event['id'])
            return

        for event in events:
            all_sent = True
            for chat_id, language in chats:
                kwargs = {
                    'voltage': event['voltage'],
                    'load': event['load_watts'],
                    'duration': event['duration'],
                    'level': event['level'],
                    'forecast_line': locales.get_forecast_line(
                        language,
                        event['forecast_action'] or '',
                        event['forecast_text'] or '',
                    ),
                }
                text = locales.get_message(language, event['message_key'], **kwargs)
                sent = False
                for attempt in range(3):
                    try:
                        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
                        sent = True
                        break
                    except Exception as e:
                        error_text = str(e)
                        if 'bot was blocked by the user' in error_text or 'chat not found' in error_text:
                            logger.warning(f"Removing unreachable chat {chat_id}: {e}")
                            try:
                                await remove_chat(self.pool, chat_id)
                            except Exception:
                                pass
                            sent = True
                            break
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                        else:
                            logger.error(f"Failed to send queued event {event['id']} to {chat_id}: {e}")
                if not sent:
                    all_sent = False

            if all_sent:
                await mark_notification_delivered(self.pool, event['id'])

    async def delivery_loop(self):
        while self.running:
            try:
                await self.deliver_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Notification delivery loop failed; will retry")
            await asyncio.sleep(5)

    async def notify(self, is_battery, voltage, load=0, duration=""):
        if is_battery:
            await self.broadcast("warning_battery", voltage=voltage, load=load, duration=duration)
        else:
            await self.broadcast("power_restored", voltage=voltage, duration=duration)

        log_msg = f"Notify: Battery={is_battery}, Voltage={voltage}, Load={load}, Duration={duration}"
        logger.info(log_msg)

    def read_inverter(self):
        inv = None
        try:
            inv = PySolarmanV5(INVERTER_IP, LOGGER_SERIAL, socket_timeout=5, verbose=False)
            data = inv.read_holding_registers(GRID_V_REG, 35)
            grid_v = data[0] / 10.0
            current_mode = data[14]
            battery_soc = data[34]
            load_watts = data[28]
            return grid_v, current_mode, battery_soc, load_watts
        except Exception as e:
            logger.error(f"Read error: {e}")
            return None, None, None, None
        finally:
            if inv:
                try:
                    inv.disconnect()
                except:
                    pass

    async def check(self):
        loop = asyncio.get_running_loop()
        try:
            grid_v, current_mode, battery_soc, load_watts = await loop.run_in_executor(None, self.read_inverter)
        except Exception as e:
            logger.error(f"Executor error: {e}")
            return

        if grid_v is None:
            return

        if self.last_battery_soc is not None and battery_soc is not None:
            if battery_soc == 100 and self.last_battery_soc < 100:
                await self.broadcast("battery_full")
            elif battery_soc <= 75 < self.last_battery_soc:
                await self.broadcast("battery_discharging", level=battery_soc)
            elif battery_soc <= 50 < self.last_battery_soc:
                await self.broadcast("battery_discharging", level=battery_soc)
            elif battery_soc <= 25 < self.last_battery_soc:
                await self.broadcast("battery_low", level=battery_soc)

        if battery_soc is not None:
            self.last_battery_soc = battery_soc

        currently_online = (grid_v > VOLTAGE_THRESHOLD) and (current_mode != 300)

        if self.is_grid_online is None:
            saved_state = await load_monitor_state(self.pool)
            if saved_state is None:
                self.is_grid_online = currently_online
                self.last_battery_soc = battery_soc
                self.last_status_time = datetime.now()
                await save_initial_monitor_state(self.pool, currently_online, self.last_status_time)
            else:
                self.is_grid_online = saved_state['is_grid_online']
                self.last_status_time = saved_state['last_status_time']
                if currently_online != self.is_grid_online:
                    now = datetime.now()
                    duration = self.format_duration(now - self.last_status_time)
                    forecast_action, forecast_text = self.schedule_provider.next_event_for_state(currently_online)
                    await record_transition(
                        self.pool,
                        currently_online,
                        now,
                        grid_v,
                        load_watts,
                        duration,
                        forecast_action,
                        forecast_text,
                    )
                    self.is_grid_online = currently_online
                    self.last_status_time = now
            self.last_battery_soc = battery_soc
            status_label = "GRID" if currently_online else "BATTERY"
            logger.info(
                f"Monitor started. Current mode: {status_label} ({current_mode}), Voltage: {grid_v}V, Battery: {battery_soc}%")
            return

        if currently_online != self.is_grid_online:
            now = datetime.now()
            duration = self.format_duration(now - self.last_status_time)
            forecast_action, forecast_text = self.schedule_provider.next_event_for_state(currently_online)
            await record_transition(
                self.pool,
                currently_online,
                now,
                grid_v,
                load_watts,
                duration,
                forecast_action,
                forecast_text,
            )
            self.last_status_time = now
            self.is_grid_online = currently_online
        else:
            status_text = "OK" if currently_online else "BATTERY"
            logger.debug(f"Mode: {current_mode}, Grid: {grid_v}V, Batt: {battery_soc}% | {status_text}")

    async def run(self):
        logger.info(f"Starting Deye Inverter Monitor for {INVERTER_IP}")
        while self.running:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Monitor iteration failed; will retry")
            await asyncio.sleep(CHECK_INTERVAL)


async def main():
    pool = await get_db_pool()
    await init_db(pool)

    schedule_provider = YasnoScheduleProvider(pool)
    monitor = DeyeStatusMonitor(pool, schedule_provider)

    monitor_task = asyncio.create_task(monitor.run())
    delivery_task = asyncio.create_task(monitor.delivery_loop())
    schedule_task = asyncio.create_task(schedule_provider.run())

    try:
        await dp.start_polling(bot, pool=pool, schedule_provider=schedule_provider)
    finally:
        monitor.running = False
        for task in (monitor_task, delivery_task, schedule_task):
            task.cancel()
        await asyncio.gather(monitor_task, delivery_task, schedule_task, return_exceptions=True)
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
