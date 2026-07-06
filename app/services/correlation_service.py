"""
correlation_service.py
-----------------------
Responsible for maintaining the "truth" about conversations and their
participants: creating new conversations, associating systems, and updating
a participant's local status when an event arrives.

Knows nothing about HTTP or the outbox - only about the relational state in
`conversations` and `conversation_participants`.
"""
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.services.audit_service import record_audit


async def find_or_create_conversation(
    conn: AsyncConnection,
    *,
    conversation_id: UUID | None,
    source: str,
    external_ref: str,
    status: str,
    subject: str | None,
) -> tuple[UUID, bool]:
    """
    Returns the conversation_id to use and a boolean indicating whether it
    was just created (True) or already existed (False).

    Rules:
      - If `conversation_id` is provided, the conversation must already exist.
      - If not provided, a new conversation is created with the source
        system as its first participant.
    """
    async with conn.cursor() as cur:
        if conversation_id is not None:
            await cur.execute(
                "SELECT conversation_id FROM conversations WHERE conversation_id = %(id)s",
                {"id": conversation_id},
            )
            row = await cur.fetchone()
            if row is None:
                raise ValueError(f"Conversation {conversation_id} does not exist.")
            created = False
        else:
            await cur.execute(
                """
                INSERT INTO conversations (subject, overall_status, metadata)
                VALUES (%(subject)s, %(status)s, '{}'::jsonb)
                RETURNING conversation_id
                """,
                {"subject": subject, "status": status},
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
            detail={"external_ref": external_ref, "status": status},
        )

    return conversation_id, created


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


async def list_other_participants(
    conn: AsyncConnection, *, conversation_id: UUID, exclude: str
) -> list[dict[str, Any]]:
    """Lists a conversation's participants except the source system - used for fan-out."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT system_code, external_ref, local_status
            FROM conversation_participants
            WHERE conversation_id = %(cid)s AND system_code != %(exclude)s
            """,
            {"cid": conversation_id, "exclude": exclude},
        )
        return await cur.fetchall()


async def update_conversation_overall_status(
    conn: AsyncConnection, *, conversation_id: UUID, overall_status: str
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE conversations SET overall_status = %(status)s WHERE conversation_id = %(cid)s",
            {"status": overall_status, "cid": conversation_id},
        )
