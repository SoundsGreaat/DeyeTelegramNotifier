"""Add report queue fields and persistent inverter telemetry.

Revision ID: 0002_reports
Revises: 0001_legacy
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from alembic import context, op
from sqlalchemy import text


revision = "0002_reports"
down_revision = "0001_legacy"
branch_labels = None
depends_on = None


def _legacy_timezone() -> str:
    """Resolve and validate the zone used to interpret legacy naive dates."""
    config_zone = context.get_context().config.attributes.get("legacy_timezone")
    cli_zone = context.get_x_argument(as_dictionary=True).get("legacy_timezone")
    zone = cli_zone or config_zone or "UTC"
    if not isinstance(zone, str) or not zone.strip():
        raise ValueError("legacy_timezone must be a non-empty IANA timezone name")
    zone = zone.strip()
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Invalid legacy_timezone {zone!r}; use an IANA timezone name") from exc
    return zone


def _sql_literal(value: str) -> str:
    """Render a validated value as an escaped PostgreSQL string literal.

    The timezone is used in ALTER TABLE's USING expression, where PostgreSQL
    does not accept a bind parameter. Doubling quotes is the SQL literal
    escaping rule; timezone validation happens before this is called.
    """
    return "'" + value.replace("'", "''") + "'"


def _guard_data_loss() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "Downgrading 0002_reports with --sql is unsupported because the data-loss guard "
            "must inspect the live database. Run an online downgrade instead."
        )

    result = op.get_bind().execute(
        text(
            """
            SELECT
                EXISTS (SELECT 1 FROM telemetry_samples LIMIT 1) AS has_telemetry,
                EXISTS (
                    SELECT 1 FROM notification_queue
                    WHERE report_key IS NOT NULL
                       OR report_payload IS NOT NULL
                       OR message_key = 'statistics'
                ) AS has_reports,
                EXISTS (
                    SELECT 1 FROM notification_queue WHERE threshold IS NOT NULL
                ) AS has_thresholds
            """
        )
    ).mappings().one()
    populated = [
        name
        for name, key in (
            ("telemetry samples", "has_telemetry"),
            ("statistics report events", "has_reports"),
            ("threshold events", "has_thresholds"),
        )
        if result[key]
    ]
    if populated:
        raise RuntimeError(
            "Downgrading 0002_reports would delete " + ", ".join(populated) + ". "
            "Preserve/export that data or re-run with -x allow_data_loss=true."
        )


def _allow_data_loss() -> bool:
    value = context.get_x_argument(as_dictionary=True).get("allow_data_loss", "")
    return str(value).strip().lower() == "true"


def upgrade() -> None:
    zone = _legacy_timezone()
    zone_literal = _sql_literal(zone)

    # Existing installations may already have these columns from the
    # application's former init_db routine. IF NOT EXISTS reconciles them.
    op.execute("ALTER TABLE notification_queue ADD COLUMN IF NOT EXISTS threshold INTEGER")
    op.execute("ALTER TABLE notification_queue ADD COLUMN IF NOT EXISTS report_key TEXT")
    op.execute("ALTER TABLE notification_queue ADD COLUMN IF NOT EXISTS report_payload JSONB")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS notification_queue_report_key_idx
        ON notification_queue (report_key) WHERE report_key IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS telemetry_samples (
            observed_at TIMESTAMPTZ PRIMARY KEY,
            is_grid_online BOOLEAN NOT NULL,
            load_watts INTEGER NOT NULL CHECK (load_watts >= 0),
            battery_soc INTEGER CHECK (battery_soc IS NULL OR (battery_soc >= 0 AND battery_soc <= 100))
        )
        """
    )

    # Fresh and legacy schemas from 0001 use TIMESTAMP WITHOUT TIME ZONE;
    # already-updated installations keep their TIMESTAMPTZ values unchanged.
    op.execute(
        f"""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'monitor_state'
                  AND column_name = 'last_status_time'
                  AND data_type = 'timestamp without time zone'
            ) THEN
                ALTER TABLE monitor_state ALTER COLUMN last_status_time
                TYPE TIMESTAMPTZ USING last_status_time AT TIME ZONE {zone_literal};
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    # Validate the zone before any database operation, including the guard.
    zone = _legacy_timezone()
    zone_literal = _sql_literal(zone)

    if not _allow_data_loss():
        _guard_data_loss()
    elif context.is_offline_mode():
        # There is no live state to inspect; an explicit opt-in permits SQL
        # generation, with the operator responsible for the resulting drops.
        pass

    # The legacy bot has no renderer for report events. The guard requires an
    # explicit opt-in whenever such events exist, so remove them deliberately.
    op.execute("DELETE FROM notification_queue WHERE message_key = 'statistics'")

    op.execute(
        f"""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'monitor_state'
                  AND column_name = 'last_status_time'
                  AND data_type = 'timestamp with time zone'
            ) THEN
                ALTER TABLE monitor_state ALTER COLUMN last_status_time
                TYPE TIMESTAMP USING last_status_time AT TIME ZONE {zone_literal};
            END IF;
        END $$
        """
    )
    op.execute("DROP INDEX IF EXISTS notification_queue_report_key_idx")
    op.execute(
        """
        ALTER TABLE notification_queue
            DROP COLUMN IF EXISTS threshold,
            DROP COLUMN IF EXISTS report_key,
            DROP COLUMN IF EXISTS report_payload
        """
    )
    op.execute("DROP TABLE IF EXISTS telemetry_samples")
