import os
import asyncio
import logging
import sys
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from pysolarmanv5 import PySolarmanV5
from dotenv import load_dotenv

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
                               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                           )
                           ''')


async def add_chat(pool, chat_id):
    async with pool.acquire() as conn:
        await conn.execute('INSERT INTO chats (chat_id) VALUES ($1) ON CONFLICT (chat_id) DO NOTHING', chat_id)


async def remove_chat(pool, chat_id):
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM chats WHERE chat_id = $1', chat_id)


async def get_chats(pool):
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT chat_id FROM chats')
        return [row['chat_id'] for row in rows]


@dp.message(Command("start"))
async def cmd_start(message: types.Message, pool: asyncpg.Pool):
    await add_chat(pool, message.chat.id)
    await message.answer("✅ You have subscribed to inverter status notifications.")


@dp.message(Command("stop"))
async def cmd_stop(message: types.Message, pool: asyncpg.Pool):
    await remove_chat(pool, message.chat.id)
    await message.answer("❌ You have unsubscribed from notifications.")


class DeyeStatusMonitor:
    def __init__(self, pool):
        self.pool = pool
        self.is_grid_online = None
        self.last_battery_soc = None
        self.running = True

    async def broadcast(self, text):
        chat_ids = await get_chats(self.pool)
        if not chat_ids:
            return

        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to send to {chat_id}: {e}")

    async def notify(self, is_battery, voltage):
        if is_battery:
            msg = f"⚠️ <b>Warning: Battery Power</b>\nGrid voltage: {voltage}V"
        else:
            msg = f"✅ <b>Power Restored</b>\nGrid voltage: {voltage}V"

        logger.info(msg.replace('\n', ' ').replace('<b>', '').replace('</b>', ''))
        await self.broadcast(msg)

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
                await self.broadcast(f"🔋 <b>Battery Fully Charged</b>\nLevel: 100%")
            elif battery_soc <= 75 < self.last_battery_soc:
                await self.broadcast(f"📉 <b>Battery Discharging</b>\nLevel: {battery_soc}%")
            elif battery_soc <= 50 < self.last_battery_soc:
                await self.broadcast(f"📉 <b>Battery Discharging</b>\nLevel: {battery_soc}%")
            elif battery_soc <= 25 < self.last_battery_soc:
                await self.broadcast(f"⚠️ <b>Battery Low</b>\nLevel: {battery_soc}%")
        
        if battery_soc is not None:
            self.last_battery_soc = battery_soc

        currently_online = (grid_v > VOLTAGE_THRESHOLD) and (current_mode != 300)

        if self.is_grid_online is None:
            self.is_grid_online = currently_online
            self.last_battery_soc = battery_soc
            status_label = "GRID" if currently_online else "BATTERY"
            logger.info(f"Monitor started. Current mode: {status_label} ({current_mode}), Voltage: {grid_v}V, Battery: {battery_soc}%")
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
