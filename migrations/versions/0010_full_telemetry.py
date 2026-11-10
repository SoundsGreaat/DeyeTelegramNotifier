"""Keep complete inverter readings for the read-only API and history charts.

Revision ID: 0010_full_telemetry
Revises: 0009_grid_presence
"""

from alembic import context, op
from sqlalchemy import text

revision = "0010_full_telemetry"
down_revision = "0009_grid_presence"
branch_labels = None
depends_on = None


_COLUMNS = (
    "grid_voltage",
    "grid_connected",
    "battery_current",
    "battery_capacity_ah",
    "shutdown_soc",
    "battery_power_watts",
    "battery_voltage",
    "battery_temperature_c",
    "inverter_dc_temperature_c",
    "inverter_radiator_temperature_c",
    "runtime_eta_seconds",
    "charge_eta_seconds",
    "battery_cutoff_soc",
)


def upgrade():
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN grid_voltage DOUBLE PRECISION")
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN grid_connected BOOLEAN")
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN battery_current DOUBLE PRECISION")
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN battery_capacity_ah DOUBLE PRECISION")
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN shutdown_soc DOUBLE PRECISION")
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN battery_power_watts INTEGER")
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN battery_voltage DOUBLE PRECISION")
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN battery_temperature_c DOUBLE PRECISION")
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN inverter_dc_temperature_c DOUBLE PRECISION")
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN inverter_radiator_temperature_c DOUBLE PRECISION")
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN runtime_eta_seconds DOUBLE PRECISION")
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN charge_eta_seconds DOUBLE PRECISION")
    op.execute("ALTER TABLE telemetry_samples ADD COLUMN battery_cutoff_soc DOUBLE PRECISION")


def downgrade():
    allowed = context.get_x_argument(as_dictionary=True).get("allow_data_loss", "").lower() in {
        "true",
        "1",
        "yes",
    }
    if not allowed:
        if context.is_offline_mode():
            raise RuntimeError(
                "Full telemetry downgrade requires an online data-loss check or -x allow_data_loss=true"
            )
        populated = (
            op.get_bind()
            .execute(
                text(
                    f"SELECT EXISTS (SELECT 1 FROM telemetry_samples WHERE "
                    f"{' OR '.join(f'{column} IS NOT NULL' for column in _COLUMNS)})"
                )
            )
            .scalar()
        )
        if populated:
            raise RuntimeError(
                "Full telemetry downgrade would remove saved inverter readings; "
                "export them first or use -x allow_data_loss=true"
            )
    op.execute(f"ALTER TABLE telemetry_samples {', '.join(f'DROP COLUMN {column}' for column in _COLUMNS)}")
