# Database migrations

The project uses [Alembic](https://alembic.sqlalchemy.org/en/latest/) with an asyncpg-backed SQLAlchemy engine for schema changes. Runtime application queries still use `asyncpg` directly; no ORM models are required.

## Ownership and startup

- `migrations/versions/` is the only place for schema DDL and data migrations.
- `alembic.ini` points to that directory and contains no credentials.
- `migrations/env.py` is shared by the Alembic CLI and application startup.
- `deye_notifier/database/migrations.py` builds the connection URL from `POSTGRES_*`, applies pending revisions, or checks the current version.
- `deye_notifier/app.py` waits for database connectivity, prepares the schema, then starts monitoring, report generation, notification delivery, and Telegram polling. A migration failure closes the pool and exits before workers start.
- `deye_notifier/database/telemetry.py` reads/writes telemetry; it does not create tables.

Each online migration command takes the same transaction-scoped PostgreSQL advisory lock **before** checking the version table. All pending revisions and their `alembic_version` updates run in one transaction. A failure rolls back that entire command, including earlier revisions from that command. The lock is released on commit, rollback, or connection loss. Lock acquisition waits at most 30 seconds; a failed startup can be retried after the other migration finishes.

This project uses transactional PostgreSQL DDL. Do not add `CREATE INDEX CONCURRENTLY`, autocommit blocks, commits inside revisions, or other operations that would break the transaction guarantee without first changing and testing the deployment design. The lock serializes migration commands, not ordinary queries from an already-running old bot. Stop the old bot when applying incompatible changes.

## Existing and new databases

The current chain is:

| Revision | Purpose |
| --- | --- |
| `0001_legacy` | Create a new baseline or adopt tables created by older releases; add legacy language/forecast fields if missing. |
| `0002_reports` | Add telemetry, battery thresholds and report queue fields; convert monitor state timestamps to timezone-aware values. |
| `0003_subscriptions` | Separate power/battery subscriptions from automatic reports; existing users keep alerts and must explicitly opt in to reports. |
| `0004_language_cleanup` | Switch saved Russian language preferences to English without changing subscriptions. |
| `0005_battery_eta` | Preserve charging time estimates with queued battery notifications. |
| `0006_measured_telemetry` | Store measured load-energy counters and battery runtime forecasts. |
| `0007_charge_power` | Preserve charging power with queued battery notifications. |
| `0008_live_status` | Persist the active live status message per chat. |
| `0009_grid_presence` | Base grid availability on measured voltage and clear incompatible saved transition state. |
| `0010_full_telemetry` | Store complete inverter measurements and battery forecasts in telemetry history for the read-only API. |

Both a fresh database and the project's known unversioned schemas can run `upgrade head`. The adoption revisions use conditional, additive SQL and preserve existing rows. Already timezone-aware values are left unchanged. There is no need to run `stamp`; stamping an unverified database would bypass the actual migrations.

`0009_grid_presence` clears the saved grid state once because earlier releases tracked the inverter relay, while the new monitor tracks measured grid voltage. The next successful sample starts a new duration. Historical energy and outage samples remain unchanged, but older samples do not contain enough information to reconstruct grid presence from voltage.

`0010_full_telemetry` adds nullable columns for grid voltage and relay state, battery current/capacity/cutoff/power/voltage/temperature, inverter temperatures, runtime and charge estimates, and effective cutoff SOC. New monitoring samples populate these columns. Existing samples remain available with `NULL` for measurements that were not recorded at the time; the migration does not fabricate historical values or delete old samples.

Schema adoption targets the known historical table definitions, not arbitrary manually modified schemas. Existing incompatible types, duplicate report keys, or insufficient database permissions cause an error and rollback rather than being silently repaired.

### Legacy timestamp timezone

Before the first versioned upgrade, set `LEGACY_DB_TIMEZONE` to the zone in which old naive `monitor_state.last_status_time` values were written:

```ini
LEGACY_DB_TIMEZONE=UTC
```

Keep UTC for the original Docker deployment. For a previous standalone deployment whose host wrote Kyiv local time, use `Europe/Kyiv`. This setting is independent of `STATS_TIMEZONE`, which controls report periods. Changing `LEGACY_DB_TIMEZONE` after migration does not reinterpret existing timezone-aware values. A standalone command can override it with `python -m alembic -x legacy_timezone=Europe/Kyiv upgrade head`.

## Docker update

Migration files and dependencies are copied into the image by the Dockerfile. Migrations run **when the container starts**, while the database is reachable; image builds do not connect to the database. Compose waits for the PostgreSQL healthcheck. `.dockerignore` excludes local credentials, Git history, the virtual environment and tests from the build context.

Keep the same Compose project and PostgreSQL volume. Back up the existing database before applying an update. These commands keep the existing `postgres_data` volume:

```bash
docker compose stop deye-monitor
docker compose up -d --build
docker compose logs -f deye-monitor
```

`DB_AUTO_MIGRATE=true` is the default. Do not use `docker compose down -v` during an update: `-v` removes database volumes. Bundled Compose retains the original `postgres` user/password and `deye_bot` database settings for compatibility with existing volumes; it supplies matching settings to the bot. Standalone commands use `POSTGRES_*` from the environment or `.env`. Changing environment variables alone does not alter users/passwords inside an existing PostgreSQL volume; deliberate credential changes require updating the database roles and both services' connection settings together.

### Separate migration step

For a deployment that applies schema updates explicitly, set `DB_AUTO_MIGRATE=false` in `.env`, then run:

```bash
docker compose stop deye-monitor
docker compose build deye-monitor
docker compose run --rm deye-monitor python -m alembic upgrade head
docker compose run --rm deye-monitor python -m alembic current
docker compose up -d deye-monitor
```

`run` starts/waits for the PostgreSQL dependency and overrides the image's bot command. It does not run the Telegram bot. A failed migration must be resolved before starting the new bot. With auto-migration disabled, the bot compares the database revisions with the revisions shipped in its image and refuses to start if they differ.

## Local commands

Install `requirements.txt` in your virtual environment. Supply `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` through `.env` or the environment. The defaults match Compose (`postgres:5432`); standalone host access usually needs a different host and a reachable port. Alembic commands never start the bot and do not require inverter settings or `TELEGRAM_TOKEN`.

```bash
python -m alembic current
python -m alembic history
python -m alembic heads
python -m alembic upgrade head
python -m alembic upgrade head --sql
```

`--sql` emits SQL for review without connecting or changing the database. The final command above emits the full baseline-to-head script, not just migrations missing from a particular database. Online commands are protected by the advisory lock; generated offline SQL is for review and does not acquire it.

## Adding a migration

1. Create a new revision locally: `python -m alembic revision -m "add descriptive schema change"`.
2. Write explicit `upgrade()` and `downgrade()` operations in the new file. Use `op.execute()` for SQL or Alembic operations. This project has no SQLAlchemy model metadata, so autogeneration is not configured.
3. Keep a single linear head. Never edit an already-deployed revision; add another revision instead. Conditional DDL is used by the initial adoption revisions; ordinary future revisions should describe a specific change from their parent.
4. Test a fresh install, an upgrade from the parent with representative rows, failure rollback, and any supported downgrade. Review data-loss and compatibility implications before shipping.
5. Commit the revision, affected application code, and tests together, using Conventional Commits.

## Rollback

Stop the bot first and deploy code compatible with the target schema. Alembic does not roll back automatically when an application image is replaced.

Downgrading `0008_live_status` requires `-x allow_data_loss=true` while active live messages are registered.

Downgrading `0006_measured_telemetry` requires `-x allow_data_loss=true` when measured energy counters have been stored; sample rows remain, but their counter values are removed.

Downgrading `0010_full_telemetry` refuses to remove populated measurement columns unless `-x allow_data_loss=true` is supplied. Export any history you need first. For example, to return from head to `0009_grid_presence`:

```bash
python -m alembic -x allow_data_loss=true downgrade 0009_grid_presence
```

This drops only the columns added by `0010_full_telemetry`; the base telemetry rows and columns from earlier revisions remain. Do not use this flag unless the additional readings have been backed up or may be discarded.

`0004_language_cleanup` is a one-way preference normalization. Downgrading that revision keeps English selected; it cannot distinguish migrated users from users who explicitly chose English later.

Downgrading `0003_subscriptions` to `0002_reports` removes the separate subscription preferences. It refuses when there are report subscribers or disabled power subscriptions unless `-x allow_data_loss=true` is supplied. With that explicit opt-in, report-only and inactive chat rows are removed so the older bot does not silently enable power alerts for them. Remaining active power subscribers are preserved. The older application sends automatic reports to all of its subscribers; deploy it only if that behavior is intended.

```bash
python -m alembic downgrade 0001_legacy
```

Downgrading `0002_reports` removes the telemetry table and report-related columns and restores naive monitor timestamps. Use the **same legacy timezone** used on upgrade so old application code receives the expected wall-clock values.

The downgrade refuses to remove telemetry, report payloads/keys/events, or battery threshold metadata when they contain data. After exporting or deliberately accepting that loss, an operator can opt in:

```bash
python -m alembic -x allow_data_loss=true downgrade 0001_legacy
```

That command deletes statistics queue events the old bot cannot render, drops telemetry/report fields, and converts timestamps. It is destructive. Downgrading to `base` additionally drops subscriber/state/notification/cache tables and always requires the same explicit opt-in. These guards do not replace a database backup. For normal recovery after a failed upgrade, fix the error and retry `upgrade head`; the failed transaction has already been rolled back.

## Verification

Run the offline suite:

```bash
python -m unittest discover -s tests -v
```

To enable PostgreSQL integration tests, point `TEST_POSTGRES_DSN` at a local test PostgreSQL database named `deye_test` (for example, `postgresql+asyncpg://test_user:test_password@127.0.0.1:5432/deye_test`). Only loopback hosts and URLs without query parameters are accepted. Supply it through your shell environment, then run the same command. The account needs permission to create/drop schemas. Each test creates a random `deye_migration_test_*` schema and drops only that schema afterwards. The suite overrides application environment variables with synthetic values, disables `.env` loading and blocks non-loopback network connections. It does not use production database settings as a fallback or contact Telegram or the inverter.

Integration coverage includes fresh and repeated upgrades, old/current unversioned database adoption with preserved rows, timezone conversion, guarded downgrade/re-upgrade, and whole-command rollback after a synthetic later revision fails. Run these tests against your supported PostgreSQL version as part of release validation.
