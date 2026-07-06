"""
conversations.py
-----------------
Endpoints de consulta (read-only) de conversas e respetivos participantes -
usado pelo frontend para o separador "Conversas" (visão operacional de
o que está correlacionado com o quê, equivalente a navegar tickets no
antigo OSTicket).
"""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.database import get_connection
from app.schemas import ConversationOut, ParticipantOut

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    limit: int = Query(default=50, le=200),
    sistema: str | None = None,
) -> list[ConversationOut]:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            if sistema:
                await cur.execute(
                    """
                    SELECT DISTINCT c.conversation_id, c.assunto, c.status_geral, c.created_at, c.updated_at
                    FROM conversations c
                    JOIN conversation_participants p ON p.conversation_id = c.conversation_id
                    WHERE p.sistema = %(sistema)s
                    ORDER BY c.updated_at DESC
                    LIMIT %(limit)s
                    """,
                    {"sistema": sistema, "limit": limit},
                )
            else:
                await cur.execute(
                    """
                    SELECT conversation_id, assunto, status_geral, created_at, updated_at
                    FROM conversations
                    ORDER BY updated_at DESC
                    LIMIT %(limit)s
                    """,
                    {"limit": limit},
                )
            conversas = await cur.fetchall()

        resultado = []
        for c in conversas:
            participantes = await _fetch_participants(conn, c["conversation_id"])
            resultado.append(ConversationOut(**c, participants=participantes))
    return resultado


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(conversation_id: UUID) -> ConversationOut:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT conversation_id, assunto, status_geral, created_at, updated_at
                FROM conversations WHERE conversation_id = %(id)s
                """,
                {"id": conversation_id},
            )
            conversa = await cur.fetchone()

        if conversa is None:
            raise HTTPException(status_code=404, detail="Conversa não encontrada.")

        participantes = await _fetch_participants(conn, conversation_id)
    return ConversationOut(**conversa, participants=participantes)


async def _fetch_participants(conn, conversation_id: UUID) -> list[ParticipantOut]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT sistema, ref_externa, status_local, updated_at
            FROM conversation_participants
            WHERE conversation_id = %(id)s
            ORDER BY sistema
            """,
            {"id": conversation_id},
        )
        rows = await cur.fetchall()
    return [ParticipantOut(**row) for row in rows]
