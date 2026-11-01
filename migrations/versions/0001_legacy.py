"""Adopt the bot's legacy PostgreSQL schema into Alembic versioning.

Revision ID: 0001_legacy
Revises:
"""

from alembic import op, context


revision = "0001_legacy"
down_revision = None
branch_labels = None
depends_on = None


def _allow_data_loss() -> bool:
    value = context.get_x_argument(as_dictionary=True).get("allow_data_loss", "")
    return str(value).strip().lower() == "true"


def upgrade() -> None:
    # IF NOT EXISTS and the additive ALTERs make this safe for both new
    # databases and unversioned installations created by the bot itself.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            chat_id BIGINT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            language VARCHAR(5) DEFAULT 'en'
        )
        """
    )
    op.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS language VARCHAR(5) DEFAULT 'en'")

    # Keep the original naive type here. Revision 0002 performs the
    # timezone-aware conversion after selecting the configured legacy zone.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS monitor_state (
            id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id = TRUE),
            is_grid_online BOOLEAN NOT NULL,
            last_status_time TIMESTAMP NOT NULL
        )
        """
    )

    op.execute(
        """
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
        """
    )
    # Older installations can have the base queue table without the forecast
    # fields. Add these without rewriting or removing any queued events.
    op.execute("ALTER TABLE notification_queue ADD COLUMN IF NOT EXISTS forecast_action VARCHAR(16)")
    op.execute("ALTER TABLE notification_queue ADD COLUMN IF NOT EXISTS forecast_text TEXT")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule_cache (
            cache_key VARCHAR(128) PRIMARY KEY,
            payload JSONB NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def downgrade() -> None:
    if not _allow_data_loss():
        raise RuntimeError(
            "Downgrading 0001_legacy drops chats, monitor_state, notification_queue, "
            "and schedule_cache. Re-run with -x allow_data_loss=true to authorize it."
        )

    op.execute("DROP TABLE IF EXISTS schedule_cache")
    op.execute("DROP TABLE IF EXISTS notification_queue")
    op.execute("DROP TABLE IF EXISTS monitor_state")
    op.execute("DROP TABLE IF EXISTS chats")
