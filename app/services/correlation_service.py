"""
correlation_service.py
-----------------------
Responsible for maintaining the "truth" about conversations and their
participants: creating new conversations, associating systems, and updating
a participant's local status when an event arrives. Also resolves fan-out
destinations from topic subscriptions (see `list_fanout_destinations`).

Knows nothing about HTTP or the outbox - only about the relational state in
`conversations`, `conversation_participants`, and
`system_topic_subscriptions`.
"""
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.services.audit_service import record_audit


class ConversationNotFound(ValueError):
    """Raised when an explicit conversation_id does not match any existing conversation."""


class TopicMismatch(ValueError):
    """Raised when a request's topic_code conflicts with a conversation's existing (immutable) topic_code."""


async def find_or_create_conversation(
    conn: AsyncConnection,
    *,
    conversation_id: UUID | None,
    source: str,
    external_ref: str,
    status: str,
    subject: str | None,
    topic_code: str | None,
) -> tuple[UUID, bool, str]:
    """
    Returns (conversation_id, created, topic_code) - `created` indicates
    whether the conversation was just created (True) or already existed
    (False); `topic_code` is always the conversation's effective (stored)
    topic, so the caller never needs a follow-up query to fan out.

    Rules:
      - If `conversation_id` is provided, the conversation must already
        exist. `topic_code`, if also provided, must match the conversation's
        stored value (topics are immutable after creation) - a mismatch
        raises TopicMismatch.
      - If not provided, a new conversation is created with the source
        system as its first participant. `topic_code` is required in this
        case (enforced by the caller before validation of subscription, see
        app/api/events.py).
    """
    async with conn.cursor() as cur:
        if conversation_id is not None:
            await cur.execute(
                "SELECT conversation_id, topic_code FROM conversations WHERE conversation_id = %(id)s",
                {"id": conversation_id},
            )
            row = await cur.fetchone()
            if row is None:
                raise ConversationNotFound(f"Conversation {conversation_id} does not exist.")
            if topic_code is not None and topic_code != row["topic_code"]:
                raise TopicMismatch(
                    f"Conversation {conversation_id} belongs to topic '{row['topic_code']}', "
                    f"not '{topic_code}' - topics are immutable after creation."
                )
            topic_code = row["topic_code"]
            created = False
        else:
            await cur.execute(
                """
                INSERT INTO conversations (subject, topic_code, overall_status, metadata)
                VALUES (%(subject)s, %(topic_code)s, %(status)s, '{}'::jsonb)
                RETURNING conversation_id
                """,
                {"subject": subject, "topic_code": topic_code, "status": status},
            )
            row = await cur.fetchone()
            conversation_id = row["conversation_id"]
            created = True

        await _upsert_participant(
            cur,
            conversation_id=conversation_id,
            system_code=source,
            external_ref=external_ref,
            local_status=status,
        )

    if created:
        await record_audit(
            conn,
            conversation_id=conversation_id,
            system_code=source,
            event_type="conversation_created",
            detail={"external_ref": external_ref, "status": status, "topic_code": topic_code},
        )

    return conversation_id, created, topic_code


async def _upsert_participant(
    cur, *, conversation_id: UUID, system_code: str, external_ref: str, local_status: str
) -> None:
    await cur.execute(
        """
        INSERT INTO conversation_participants
            (conversation_id, system_code, external_ref, local_status)
        VALUES (%(cid)s, %(system_code)s, %(ref)s, %(status)s)
        ON CONFLICT (conversation_id, system_code)
        DO UPDATE SET
            external_ref = EXCLUDED.external_ref,
            local_status = EXCLUDED.local_status,
            updated_at = now()
        """,
        {"cid": conversation_id, "system_code": system_code, "ref": external_ref, "status": local_status},
    )


async def get_participant_status(
    conn: AsyncConnection, *, conversation_id: UUID, system_code: str
) -> str | None:
    """Returns the local_status currently stored for (conversation, system), or None if it doesn't exist."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT local_status FROM conversation_participants
            WHERE conversation_id = %(cid)s AND system_code = %(system_code)s
            """,
            {"cid": conversation_id, "system_code": system_code},
        )
        row = await cur.fetchone()
    return row["local_status"] if row else None


async def list_fanout_destinations(
    conn: AsyncConnection, *, conversation_id: UUID, topic_code: str, exclude: str
) -> list[dict[str, Any]]:
    """
    Lists the fan-out destinations for a conversation: every active system
    subscribed to `topic_code`, except `exclude` (the event's source).

    Destinations are driven strictly by current topic subscriptions, not by
    prior participation - a system that unsubscribes from a topic stops
    receiving fan-out for it immediately, even for conversations it already
    has a ticket linked to.

    Each row also reports whether a `conversation_participants` row already
    exists for that destination (`is_known_participant`) via a LEFT JOIN,
    purely so the caller can shape the outbound payload as "update your
    ticket X" vs. "please open a new ticket" - it does not affect which
    systems are selected.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT
                s.code AS system_code,
                cp.external_ref,
                cp.local_status,
                (cp.conversation_id IS NOT NULL) AS is_known_participant
            FROM systems s
            JOIN system_topic_subscriptions sub
                ON sub.system_code = s.code AND sub.topic_code = %(topic_code)s
            LEFT JOIN conversation_participants cp
                ON cp.conversation_id = %(cid)s AND cp.system_code = s.code
            WHERE s.active = TRUE AND s.code != %(exclude)s
            """,
            {"cid": conversation_id, "topic_code": topic_code, "exclude": exclude},
        )
        return await cur.fetchall()


async def is_system_subscribed(conn: AsyncConnection, *, system_code: str, topic_code: str) -> bool:
    """Returns whether `system_code` is currently subscribed to `topic_code`."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT 1 FROM system_topic_subscriptions
            WHERE system_code = %(system_code)s AND topic_code = %(topic_code)s
            """,
            {"system_code": system_code, "topic_code": topic_code},
        )
        return await cur.fetchone() is not None


async def update_conversation_overall_status(
    conn: AsyncConnection, *, conversation_id: UUID, overall_status: str
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE conversations SET overall_status = %(status)s WHERE conversation_id = %(cid)s",
            {"status": overall_status, "cid": conversation_id},
        )
