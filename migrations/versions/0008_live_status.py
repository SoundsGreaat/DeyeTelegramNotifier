"""Persist the single active live status message for each chat.

Revision ID: 0008_live_status
Revises: 0007_charge_power
"""

from alembic import context, op
from sqlalchemy import text

revision = "0008_live_status"
down_revision = "0007_charge_power"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE live_status_messages (
        chat_id BIGINT PRIMARY KEY REFERENCES chats(chat_id) ON DELETE CASCADE,
        message_id BIGINT NOT NULL
    )""")


def downgrade():
    allowed = context.get_x_argument(as_dictionary=True).get("allow_data_loss", "").lower() in {
        "true",
        "1",
        "yes",
    }
    if not allowed:
        if context.is_offline_mode():
            raise RuntimeError("Live status downgrade requires an online check or -x allow_data_loss=true")
        if op.get_bind().execute(text("SELECT EXISTS (SELECT 1 FROM live_status_messages)")).scalar():
            raise RuntimeError(
                "Downgrade removes active live status preferences; use -x allow_data_loss=true"
            )
    op.execute("DROP TABLE live_status_messages")
