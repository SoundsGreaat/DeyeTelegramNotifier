"""Persist charging forecasts with queued battery notifications.

Revision ID: 0005_battery_eta
Revises: 0004_language_cleanup
"""

from alembic import op

revision = "0005_battery_eta"
down_revision = "0004_language_cleanup"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE notification_queue ADD COLUMN charge_eta_seconds DOUBLE PRECISION")


def downgrade():
    # The remaining notification data is still readable by the older bot.
    op.execute("ALTER TABLE notification_queue DROP COLUMN charge_eta_seconds")
