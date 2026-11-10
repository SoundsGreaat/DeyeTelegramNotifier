# Deye Inverter Telegram Notifier

A Telegram bot for monitoring a Deye hybrid inverter over the Solarman V5 protocol.

## Features

- Power outage and restoration alerts, including how long the previous state lasted.
- Battery charging and discharging notifications at 25% milestones.
- Live status with load, battery level, grid-state duration, battery runtime, and estimated time to full charge.
- Daily, weekly, and monthly electricity reports, on demand or by subscription.
- Persistent telemetry and subscriptions in PostgreSQL.
- English, Ukrainian, Spanish, and German messages.
- Optional outage schedule forecasts and Docker Compose deployment.

## Setup

You need Docker Compose, a Telegram bot token from [BotFather](https://t.me/BotFather), and the inverter's IP address and logger serial number.

Create `.env` from [.env.example](.env.example) and set:

```ini
INVERTER_IP=192.168.1.151
LOGGER_SERIAL=1234567890
TELEGRAM_TOKEN=your_telegram_bot_token
```

Battery forecasts use the inverter’s battery current, configured capacity in Ah, and shutdown SOC when available. `BATTERY_CAPACITY_KWH`, `BATTERY_CUTOFF_SOC`, and `BATTERY_INVERTER_EFFICIENCY` provide fallback runtime estimates; set capacity to the total nominal kWh of all batteries. These settings do not reconfigure the inverter. `MIN_LOAD_WATTS` (default `10`) filters small loads from outage notifications and runtime estimates.

Grid availability follows incoming voltage above 100 V, independently of the inverter’s delayed grid relay. Voltage warnings use `GRID_VOLTAGE_MIN`/`GRID_VOLTAGE_MAX` (default `180`/`260` V), with `GRID_VOLTAGE_HYSTERESIS` (default `5` V) for recovery. Inverter disconnection and reconnection have separate alerts; these settings do not change its protection limits.

Report settings:

| Setting | Purpose |
| --- | --- |
| `STATS_TIMEZONE` | Calendar/report timezone, for example `Europe/Kyiv`. Defaults to UTC if unset. |
| `STATS_REPORT_HOUR` | Local delivery hour, from 0 to 23. Defaults to 9. |
| `STATS_AUTO_REPORTS` | Global availability of automatic reports. Each chat must subscribe separately. |

Optional schedule forecasts use the `SCHEDULE_*` settings in `.env.example`. Leave `SCHEDULE_PROVIDER` empty to disable them.

Start or update the application:

```bash
docker compose up -d --build
```

View logs or stop the services:

```bash
docker compose logs -f deye-monitor
docker compose down
```

PostgreSQL data is stored in the `postgres_data` volume. Keep that volume across updates.

## Read-only API

An optional token-protected API exposes live status, telemetry history, statistics, events, and cached outage forecasts for Home Assistant. It is disabled by default. See the [API and Home Assistant guide](docs/api.md) for setup.

## Bot commands

The bot registers its command menu with English descriptions at startup.

| Command | Action |
| --- | --- |
| `/start` | Enable power and battery notifications. |
| `/stop` | Disable all notifications and automatic reports. |
| `/status` | Show live inverter status and estimated battery runtime. |
| `/live` | Create a pinned detailed status; replace the previous live message. |
| `/live_off` | Stop updates and unpin the live message. |
| `/stats` or `/stats day` or `/stats_day` | Today's electricity report. |
| `/stats week` or `/stats_week` | This week's report, starting Monday. |
| `/stats month` or `/stats_month` | This month's report, starting on the first day. |
| `/stats_auto_on` | Subscribe to automatic daily, weekly, and monthly reports. |
| `/stats_auto_off` | Disable automatic reports. |
| `/lang` | Choose the language of bot messages. |

`/live` updates one message every `LIVE_STATUS_INTERVAL` seconds (default `60`) using existing inverter readings. It resumes after restarts; `/stop` also stops it. In groups, pinning requires the bot’s permission to pin messages.

`/start` does not enable automatic reports. Report subscriptions are independent of power alerts, so `/stats_auto_on` can also be used on its own.

Automatic reports cover the previous day, previous week, or previous month and are sent at `STATS_REPORT_HOUR` in `STATS_TIMEZONE`.

Reports use the inverter’s load-energy counter when available, falling back to sampled load. They include consumption, average and peak load, grid availability, observed outages, battery range, and data coverage. Missing readings remain unknown. Battery runtime remains an estimate based on current consumption and usable battery reserve.

## Database migrations

Alembic manages the database schema and applies pending migrations before the bot starts. Set `DB_AUTO_MIGRATE=false` if migrations are applied separately during deployment.

See [migration guide](docs/migrations.md) for upgrade, rollback, and revision-authoring instructions.

## Tests

Install `requirements-dev.txt`, then run:

```bash
python -m unittest discover -s tests -v
```

Tests use synthetic data and mocked Telegram/inverter connections. PostgreSQL integration tests require an explicit `TEST_POSTGRES_DSN` pointing to a local database named `deye_test`; otherwise they are skipped.

GitHub Actions runs the full suite on pushes and pull requests using Python 3.11 and a disposable PostgreSQL 17 service. No production credentials or `.env` file are required.

## Project structure

```text
deye_notifier/
  app.py          # Startup, dependencies and shutdown
  config.py       # Environment settings
  bot/            # Telegram commands, menu and messages
  database/       # Connections, repositories and migration runner
  inverter/       # Solarman client and status monitoring
  services/       # Notification delivery, reports and schedule forecasts
  statistics.py   # Report periods and energy calculations
migrations/       # Alembic schema revisions
tests/
  unit/           # Component tests with mocked external services
  integration/    # PostgreSQL migration and persistence tests
docs/             # Deployment and migration details
```

For a local run, install `requirements.txt` and use `python -m deye_notifier` from the project root. `python main.py` is also supported.
