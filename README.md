# Deye Inverter Telegram Notifier

A Telegram bot that monitors the status of a Deye hybrid inverter using the Solarman V5 protocol and sends asynchronous notifications to subscribed users when the power source changes (Grid vs Battery).

## Features

- **Real-time Monitoring**: Checks inverter status periodically.
- **Telegram Notifications**: Alerts subscribed users about power outages and restoration.
- **Multi-user Support**: Users can subscribe/unsubscribe via the bot.
- **Database Storage**: Uses PostgreSQL to store subscriber data persistently.
- **Dockerized**: Easy deployment with Docker and Docker Compose.

## Prerequisites

- Docker
- Docker Compose
- A Telegram Bot Token (obtained from [@BotFather](https://t.me/BotFather))
- Deye Inverter IP address and Logger Serial Number

## Configuration

1. Clone this repository.
2. Create a `.env` file in the root directory with the following variables:

```ini
# Inverter Settings
INVERTER_IP=192.168.1.151
LOGGER_SERIAL=1234567890
CHECK_INTERVAL=10  # Seconds between checks

# Telegram Settings
TELEGRAM_TOKEN=your_telegram_bot_token

# Logging
LOG_LEVEL=INFO

# Database Settings (optional, defaults provided in docker-compose)
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=deye_bot
```

## Running the Application

Start the services using Docker Compose:

```bash
docker-compose up -d --build
```

This will start two containers:
- `deye_monitor`: The Python application and Telegram bot.
- `deye_postgres`: The PostgreSQL database.

To view logs:

```bash
docker-compose logs -f
```

To stop the application:

```bash
docker-compose down
```

## Bot Commands

- `/start` - Subscribe to status notifications.
- `/stop` - Unsubscribe from notifications.

## Technical Details

The application uses:
- `aiogram` for the asynchronous Telegram bot.
- `pysolarmanv5` for communicating with the Deye inverter.
- `asyncpg` for asynchronous PostgreSQL database interaction.
- `docker-compose` for orchestration.
