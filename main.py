import os
import time
import logging
import signal
import sys
import requests
from dotenv import load_dotenv
from pysolarmanv5 import PySolarmanV5

load_dotenv()

INVERTER_IP = os.getenv('INVERTER_IP', '192.168.1.151')
try:
    LOGGER_SERIAL = int(os.getenv('LOGGER_SERIAL', '0'))
except ValueError:
    print("Error: LOGGER_SERIAL must be an integer")
    sys.exit(1)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '10'))
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

GRID_V_REG = 150
MODE_REG = 164
VOLTAGE_THRESHOLD = 100

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class DeyeStatusMonitor:
    def __init__(self):
        self.is_grid_online = None
        self.running = True

        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            logger.warning("Telegram settings are missing. Notifications will be printed to log only.")

    def send_telegram(self, text):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            return

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

    def notify(self, is_battery, voltage):
        if is_battery:
            msg = f"⚠️ <b>Warning: Battery Power</b>\nGrid lost or voltage dropped to {voltage}V"
        else:
            msg = f"✅ <b>Power Restored</b>\nCurrent voltage: {voltage}V"

        logger.info(msg.replace('\n', ' ').replace('<b>', '').replace('</b>', ''))
        self.send_telegram(msg)

    def check(self):
        inv = None
        try:
            inv = PySolarmanV5(INVERTER_IP, LOGGER_SERIAL, socket_timeout=5, verbose=False)

            data = inv.read_holding_registers(GRID_V_REG, 15)

            grid_v = data[0] / 10.0
            current_mode = data[14]

            currently_online = (grid_v > VOLTAGE_THRESHOLD) and (current_mode != 300)

            if self.is_grid_online is None:
                self.is_grid_online = currently_online
                status_label = "GRID" if currently_online else "BATTERY"
                logger.info(f"Monitor started. Current mode: {status_label} ({current_mode}), Voltage: {grid_v}V")
                return

            if currently_online != self.is_grid_online:
                self.is_grid_online = currently_online
                self.notify(not currently_online, grid_v)
            else:
                status_text = "OK" if currently_online else "BATTERY"
                logger.debug(f"Mode: {current_mode}, Grid: {grid_v}V | {status_text}")

        except Exception as e:
            logger.error(f"Connection/Read error: {e}")
        finally:
            if inv:
                try:
                    inv.disconnect()
                except:
                    pass

    def run(self):
        logger.info(f"Starting Deye Inverter Monitor for {INVERTER_IP}")
        while self.running:
            self.check()
            time.sleep(CHECK_INTERVAL)

    def stop(self, signum, frame):
        logger.info("Stopping monitor...")
        self.running = False


if __name__ == "__main__":
    monitor = DeyeStatusMonitor()

    signal.signal(signal.SIGINT, monitor.stop)
    signal.signal(signal.SIGTERM, monitor.stop)

    monitor.run()
