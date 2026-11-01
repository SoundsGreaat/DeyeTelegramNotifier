"""Separate power notifications from opt-in automatic electricity reports.

Revision ID: 0003_subscriptions
Revises: 0002_reports
"""

from alembic import context, op
from sqlalchemy import text

revision = '0003_subscriptions'
down_revision = '0002_reports'
branch_labels = None
depends_on = None


def upgrade():
    # Existing subscribers retain power alerts, but nobody is implicitly
    # subscribed to daily reports by an earlier /start command.
    op.execute('ALTER TABLE chats ADD COLUMN notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE')
    op.execute('ALTER TABLE chats ADD COLUMN stats_enabled BOOLEAN NOT NULL DEFAULT FALSE')


def downgrade():
    allow_data_loss = context.get_x_argument(as_dictionary=True).get('allow_data_loss', '').lower() == 'true'
    if not allow_data_loss:
        if context.is_offline_mode():
            raise RuntimeError('Subscription downgrade requires a live database or -x allow_data_loss=true')
        changed = op.get_bind().execute(text('''
            SELECT EXISTS(SELECT 1 FROM chats WHERE stats_enabled OR NOT notifications_enabled)
        ''')).scalar_one()
        if changed:
            raise RuntimeError('Downgrade would lose subscription preferences; use -x allow_data_loss=true to accept this')
    # Older code treats every chat row as a power subscriber. Remove inactive
    # and report-only rows rather than silently subscribing them to alerts.
    op.execute('DELETE FROM chats WHERE NOT notifications_enabled')
    op.execute('ALTER TABLE chats DROP COLUMN stats_enabled, DROP COLUMN notifications_enabled')
