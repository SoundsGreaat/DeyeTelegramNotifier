import os
import asyncio
import logging
import sys
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
            return await asyncpg.create_pool(user=PG_USER, password=PG_PASS, database=PG_DB, host=PG_HOST)
        except Exception as e:
            logger.warning(f"Waiting for database... ({e})")
            await asyncio.sleep(2)
    raise Exception("Could not connect to database")


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
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT chat_id, language FROM chats')
        return [(row['chat_id'], row['language']) for row in rows]


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
async def cmd_status(message: types.Message):
    user_lang = message.from_user.language_code if message.from_user.language_code in locales.LANGUAGES else 'en'
    wait_msg = await message.answer("⏳")

    def read_inv():
        inv = None
        try:
            inv = PySolarmanV5(INVERTER_IP, LOGGER_SERIAL, socket_timeout=5, verbose=False)
            data = inv.read_holding_registers(GRID_V_REG, 35)
            return data[0] / 10.0, data[14], data[34]
        except Exception as e:
            logger.error(f"Manual check error: {e}")
            return None, None, None
        finally:
            if inv:
                try:
                    inv.disconnect()
                except:
                    pass

    loop = asyncio.get_running_loop()
    grid_v, current_mode, battery_soc = await loop.run_in_executor(None, read_inv)

    if grid_v is None:
        await wait_msg.edit_text("❌ Connection failed")
        return

    is_online = (grid_v > VOLTAGE_THRESHOLD) and (current_mode != 300)
    grid_status_text = locales.get_message(user_lang, "online" if is_online else "offline")

    text = locales.get_message(user_lang, "status",
                               grid_status=grid_status_text,
                               voltage=grid_v,
                               battery=battery_soc)

    await wait_msg.edit_text(text, parse_mode=ParseMode.HTML)


class DeyeStatusMonitor:
    def __init__(self, pool):
        self.pool = pool
        self.is_grid_online = None
        self.last_battery_soc = None
        self.running = True

    async def broadcast(self, key, **kwargs):
        chats = await get_chats(self.pool)
        if not chats:
            return

        for chat_id, language in chats:
            try:
                text = locales.get_message(language, key, **kwargs)
                await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to send to {chat_id}: {e}")

    async def notify(self, is_battery, voltage):
        if is_battery:
            await self.broadcast("warning_battery", voltage=voltage)
        else:
            await self.broadcast("power_restored", voltage=voltage)

        log_msg = f"Notify: Battery={is_battery}, Voltage={voltage}"
        logger.info(log_msg)

    def read_inverter(self):
        inv = None
        try:
            inv = PySolarmanV5(INVERTER_IP, LOGGER_SERIAL, socket_timeout=5, verbose=False)
            data = inv.read_holding_registers(GRID_V_REG, 35)
            grid_v = data[0] / 10.0
            current_mode = data[14]
            battery_soc = data[34]
            return grid_v, current_mode, battery_soc
        except Exception as e:
            logger.error(f"Read error: {e}")
            return None, None, None
        finally:
            if inv:
                try:
                    inv.disconnect()
                except:
                    pass

    async def check(self):
        loop = asyncio.get_running_loop()
        try:
            grid_v, current_mode, battery_soc = await loop.run_in_executor(None, self.read_inverter)
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
            self.is_grid_online = currently_online
            self.last_battery_soc = battery_soc
            status_label = "GRID" if currently_online else "BATTERY"
            logger.info(
                f"Monitor started. Current mode: {status_label} ({current_mode}), Voltage: {grid_v}V, Battery: {battery_soc}%")
            return

        if currently_online != self.is_grid_online:
            self.is_grid_online = currently_online
            await self.notify(not currently_online, grid_v)
        else:
            status_text = "OK" if currently_online else "BATTERY"
            logger.debug(f"Mode: {current_mode}, Grid: {grid_v}V, Batt: {battery_soc}% | {status_text}")

    async def run(self):
        logger.info(f"Starting Deye Inverter Monitor for {INVERTER_IP}")
        while self.running:
            await self.check()
            await asyncio.sleep(CHECK_INTERVAL)


async def main():
    pool = await get_db_pool()
    await init_db(pool)

    monitor = DeyeStatusMonitor(pool)

    monitor_task = asyncio.create_task(monitor.run())

    try:
        await dp.start_polling(bot, pool=pool)
    finally:
        monitor.running = False
        await monitor_task
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
