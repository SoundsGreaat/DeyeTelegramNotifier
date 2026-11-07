"""Base grid state and queued transition alerts on voltage presence.

Revision ID: 0009_grid_presence
Revises: 0008_live_status
"""

from alembic import op

revision = "0009_grid_presence"
down_revision = "0008_live_status"
branch_labels = None
depends_on = None


def _mark_pending_delivered(message_keys):
    keys = ", ".join(f"'{key}'" for key in message_keys)
    op.execute(
        "UPDATE notification_queue "
        "SET delivered_at = CURRENT_TIMESTAMP "
        "WHERE delivered_at IS NULL AND message_key IN (" + keys + ")"
    )


def upgrade():
    # The old singleton represents relay state; its timestamps cannot be
    # translated to a state based on whether grid voltage is present.
    op.execute("DELETE FROM monitor_state WHERE id = TRUE")
    _mark_pending_delivered(("warning_battery", "power_restored", "outage_load"))


def downgrade():
    # The same state row cannot be interpreted safely by the older relay-based monitor.
    op.execute("DELETE FROM monitor_state WHERE id = TRUE")
    _mark_pending_delivered(
        (
            "grid_voltage_high",
            "grid_voltage_low",
            "grid_voltage_normal",
            "grid_disconnected",
            "grid_reconnected",
        )
    )
