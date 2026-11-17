"""Store per-chat notification overrides."""

from alembic import context, op
from sqlalchemy import text

revision = "0011_notification_preferences"
down_revision = "0010_full_telemetry"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE chats ADD COLUMN notification_preferences JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade():
    allow_data_loss = context.get_x_argument(as_dictionary=True).get("allow_data_loss", "").lower() in {
        "true",
        "1",
        "yes",
    }
    if not allow_data_loss:
        if context.is_offline_mode():
            raise RuntimeError(
                "Notification preferences downgrade requires an online data-loss check "
                "or -x allow_data_loss=true"
            )
        populated = (
            op.get_bind()
            .execute(
                text("SELECT EXISTS (SELECT 1 FROM chats WHERE notification_preferences <> '{}'::jsonb)")
            )
            .scalar()
        )
        if populated:
            raise RuntimeError(
                "Notification preferences downgrade would remove saved per-chat overrides; "
                "export them first or use -x allow_data_loss=true"
            )
    op.execute("ALTER TABLE chats DROP COLUMN notification_preferences")
