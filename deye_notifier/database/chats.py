"""Chat subscription and language persistence."""

import json

from deye_notifier.notification_preferences import PREFERENCE_KEYS, REPORT_KEYS

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
                """UPDATE chats SET notifications_enabled = FALSE, stats_enabled = FALSE,
                       notification_preferences = '{}'::jsonb WHERE chat_id = $1""",
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
                ON CONFLICT (chat_id) DO UPDATE SET stats_enabled = EXCLUDED.stats_enabled,
                    notification_preferences = chats.notification_preferences - $4::text[]
                RETURNING language
                """,
                chat_id,
                language,
                enabled,
                list(REPORT_KEYS.values()),
            )

    return _normalise_language(await db_retry(pool, operation))


def _effective_preferences(row, language):
    if row is None:
        return _normalise_language(language), dict.fromkeys(PREFERENCE_KEYS, False)
    overrides = row["notification_preferences"]
    if isinstance(overrides, str):
        overrides = json.loads(overrides)
    return _normalise_language(row["language"]), {
        key: overrides.get(
            key, row["stats_enabled"] if key in REPORT_KEYS.values() else row["notifications_enabled"]
        )
        for key in PREFERENCE_KEYS
    }


async def get_notification_preferences(pool, chat_id, language="en"):
    async def operation():
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                """SELECT language, notifications_enabled, stats_enabled, notification_preferences
                   FROM chats WHERE chat_id = $1""",
                chat_id,
            )

    return _effective_preferences(await db_retry(pool, operation), language)


async def set_notification_preference(pool, chat_id, key, enabled, language="en"):
    if key not in PREFERENCE_KEYS or not isinstance(enabled, bool):
        raise ValueError("Invalid notification preference")
    override = json.dumps({key: enabled})

    async def operation():
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                """
                INSERT INTO chats (chat_id, language, notifications_enabled, stats_enabled,
                                   notification_preferences)
                VALUES ($1, $2, FALSE, FALSE, $3::jsonb)
                ON CONFLICT (chat_id) DO UPDATE SET
                    notification_preferences = chats.notification_preferences || EXCLUDED.notification_preferences
                RETURNING language, notifications_enabled, stats_enabled, notification_preferences
                """,
                chat_id,
                _normalise_language(language),
                override,
            )

    return _effective_preferences(await db_retry(pool, operation), language)


async def get_chats(pool, *, stats_only=False, preference=None):
    if preference is not None and preference not in PREFERENCE_KEYS:
        raise ValueError("Invalid notification preference")

    async def operation():
        async with pool.acquire() as conn:
            baseline = "stats_enabled" if stats_only else "notifications_enabled"
            query = f"SELECT chat_id, language FROM chats WHERE {baseline}"
            if preference is None:
                rows = await conn.fetch(query)
            else:
                rows = await conn.fetch(
                    f"""SELECT chat_id, language FROM chats
                        WHERE COALESCE((notification_preferences ->> $1::text)::boolean, {baseline})""",
                    preference,
                )
            return [(row["chat_id"], _normalise_language(row["language"])) for row in rows]

    return await db_retry(pool, operation)


async def get_chat_language(pool, chat_id):
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT language FROM chats WHERE chat_id = $1", chat_id)
    return _normalise_language(value)
