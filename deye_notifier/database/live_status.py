"""One opt-in, editable status message per chat."""

from .connection import db_retry


async def ensure_preferences(pool, chat_id, language):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO chats (chat_id, language, notifications_enabled, stats_enabled)
                   VALUES ($1, $2, FALSE, FALSE) ON CONFLICT (chat_id) DO NOTHING""",
                chat_id,
                language,
            )

    await db_retry(pool, operation)


async def get_entry(pool, chat_id):
    async def operation():
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                """SELECT l.chat_id, l.message_id, c.language FROM live_status_messages l
                   JOIN chats c ON c.chat_id = l.chat_id WHERE l.chat_id = $1""",
                chat_id,
            )

    return await db_retry(pool, operation)


async def get_entries(pool):
    async def operation():
        async with pool.acquire() as conn:
            return await conn.fetch(
                """SELECT l.chat_id, l.message_id, c.language FROM live_status_messages l
                   JOIN chats c ON c.chat_id = l.chat_id ORDER BY l.chat_id"""
            )

    return await db_retry(pool, operation)


async def save_entry(pool, chat_id, message_id):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO live_status_messages (chat_id, message_id) VALUES ($1, $2)
                   ON CONFLICT (chat_id) DO UPDATE SET message_id = EXCLUDED.message_id""",
                chat_id,
                message_id,
            )

    await db_retry(pool, operation)


async def delete_entry(pool, chat_id, message_id):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM live_status_messages WHERE chat_id = $1 AND message_id = $2",
                chat_id,
                message_id,
            )

    await db_retry(pool, operation)
