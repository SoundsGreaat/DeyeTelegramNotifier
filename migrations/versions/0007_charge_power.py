"""Preserve charging power in queued battery notifications.

Revision ID: 0007_charge_power
Revises: 0006_measured_telemetry
"""

from alembic import op

revision = "0007_charge_power"
down_revision = "0006_measured_telemetry"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE notification_queue ADD COLUMN charge_power_watts INTEGER")


def downgrade():
    # Older bots can still deliver the original notifications without this detail.
    op.execute("ALTER TABLE notification_queue DROP COLUMN charge_power_watts")
