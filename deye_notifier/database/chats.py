"""Chat subscription and language persistence."""

from .connection import db_retry


def _normalise_language(language):
    # Import only when a chat operation runs, so importing persistence modules
    # does not import bot runtime code or create application side effects.
    from deye_notifier.bot import locales

    return locales.normalize_language(language)


async def add_chat(pool, chat_id, language="en"):
    language = _normalise_language(language)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO chats (chat_id, language, notifications_enabled)
            VALUES ($1, $2, TRUE)
            ON CONFLICT (chat_id) DO UPDATE SET language = $2, notifications_enabled = TRUE
            """,
            chat_id,
            language,
        )


async def set_chat_language(pool, chat_id, language):
    language = _normalise_language(language)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE chats SET language = $1 WHERE chat_id = $2", language, chat_id)


async def remove_chat(pool, chat_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM chats WHERE chat_id = $1", chat_id)


async def unsubscribe_chat(pool, chat_id):
    async def operation():
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE chats SET notifications_enabled = FALSE, stats_enabled = FALSE WHERE chat_id = $1",
                chat_id,
            )

    await db_retry(pool, operation)


async def set_stats_subscription(pool, chat_id, enabled, language="en"):
    language = _normalise_language(language)

    async def operation():
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """
                INSERT INTO chats (chat_id, language, notifications_enabled, stats_enabled)
                VALUES ($1, $2, FALSE, $3)
                ON CONFLICT (chat_id) DO UPDATE SET stats_enabled = EXCLUDED.stats_enabled
                RETURNING language
                """,
                chat_id,
                language,
                enabled,
            )

    return _normalise_language(await db_retry(pool, operation))


async def get_chats(pool, *, stats_only=False):
    async def operation():
        async with pool.acquire() as conn:
            query = (
                "SELECT chat_id, language FROM chats WHERE stats_enabled"
                if stats_only
                else "SELECT chat_id, language FROM chats WHERE notifications_enabled"
            )
            rows = await conn.fetch(query)
            return [(row["chat_id"], _normalise_language(row["language"])) for row in rows]

    return await db_retry(pool, operation)


async def get_chat_language(pool, chat_id):
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT language FROM chats WHERE chat_id = $1", chat_id)
    return _normalise_language(value)
