"""Use measured load energy and retain battery forecast inputs.

Revision ID: 0006_measured_telemetry
Revises: 0005_battery_eta
"""

from alembic import context, op
from sqlalchemy import text

revision = "0006_measured_telemetry"
down_revision = "0005_battery_eta"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN total_load_energy_kwh DOUBLE PRECISION")
    op.execute("ALTER TABLE notification_queue ADD COLUMN runtime_eta_seconds DOUBLE PRECISION")
    op.execute("ALTER TABLE notification_queue ADD COLUMN battery_cutoff_soc DOUBLE PRECISION")


def downgrade():
    allowed = context.get_x_argument(as_dictionary=True).get("allow_data_loss", "").lower() in {
        "true",
        "1",
        "yes",
    }
    if not allowed:
        if context.is_offline_mode():
            raise RuntimeError(
                "Measured telemetry downgrade requires an online data-loss check or -x allow_data_loss=true"
            )
        populated = (
            op.get_bind()
            .execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM telemetry_samples WHERE total_load_energy_kwh IS NOT NULL)"
                )
            )
            .scalar()
        )
        if populated:
            raise RuntimeError(
                "Downgrade would remove measured energy history; use -x allow_data_loss=true after exporting it"
            )
    op.execute("ALTER TABLE notification_queue DROP COLUMN battery_cutoff_soc")
    op.execute("ALTER TABLE notification_queue DROP COLUMN runtime_eta_seconds")
    op.execute("ALTER TABLE telemetry_samples DROP COLUMN total_load_energy_kwh")
