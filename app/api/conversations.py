"""
conversations.py
-----------------
Read-only query endpoints for conversations and their participants - used
by the frontend's "Conversations" tab (an operational view of what's
correlated with what, equivalent to browsing tickets in the old OSTicket).
"""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.database import get_connection
from app.schemas import ConversationOut, ParticipantOut

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    limit: int = Query(default=50, le=200),
    system_code: str | None = None,
    topic_code: str | None = None,
) -> list[ConversationOut]:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            select_prefix = "SELECT"
            join_clause = ""
            filters = []
            params: dict = {"limit": limit}

            if system_code:
                select_prefix = "SELECT DISTINCT"
                join_clause = "JOIN conversation_participants p ON p.conversation_id = c.conversation_id"
                filters.append("p.system_code = %(system_code)s")
                params["system_code"] = system_code
            if topic_code:
                filters.append("c.topic_code = %(topic_code)s")
                params["topic_code"] = topic_code

            where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

            await cur.execute(
                f"""
                {select_prefix} c.conversation_id, c.subject, c.topic_code, c.overall_status,
                       c.created_at, c.updated_at
                FROM conversations c
                {join_clause}
                {where_clause}
                ORDER BY c.updated_at DESC
                LIMIT %(limit)s
                """,
                params,
            )
            conversations = await cur.fetchall()

        result = []
        for c in conversations:
            participants = await _fetch_participants(conn, c["conversation_id"])
            result.append(ConversationOut(**c, participants=participants))
    return result


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(conversation_id: UUID) -> ConversationOut:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT conversation_id, subject, topic_code, overall_status, created_at, updated_at
                FROM conversations WHERE conversation_id = %(id)s
                """,
                {"id": conversation_id},
            )
            conversation = await cur.fetchone()

        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")

        participants = await _fetch_participants(conn, conversation_id)
    return ConversationOut(**conversation, participants=participants)


async def _fetch_participants(conn, conversation_id: UUID) -> list[ParticipantOut]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT system_code, external_ref, local_status, updated_at
            FROM conversation_participants
            WHERE conversation_id = %(id)s
            ORDER BY system_code
            """,
            {"id": conversation_id},
        )
        rows = await cur.fetchall()
    return [ParticipantOut(**row) for row in rows]
