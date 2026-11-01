"""Move retired Russian language preferences to the default English locale.

Revision ID: 0004_language_cleanup
Revises: 0003_subscriptions
"""

from alembic import op

revision = '0004_language_cleanup'
down_revision = '0003_subscriptions'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE chats SET language = 'en' WHERE language = 'ru'")


def downgrade():
    # Preference cleanup is intentional: there is no way to distinguish a
    # migrated English user from one who explicitly selected English later.
    pass
