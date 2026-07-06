"""
correlation_service.py
-----------------------
Responsável por manter a "verdade" sobre conversas e os seus participantes:
criar novas conversas, associar sistemas, e atualizar o status local de um
participante quando chega um evento.

Não sabe nada sobre HTTP nem sobre a outbox - só sobre o estado relacional
em `conversations` e `conversation_participants`.
"""
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.services.audit_service import record_audit


async def find_or_create_conversation(
    conn: AsyncConnection,
    *,
    conversation_id: UUID | None,
    origem: str,
    ref_externa: str,
    status: str,
    assunto: str | None,
) -> tuple[UUID, bool]:
    """
    Devolve o conversation_id a usar e um booleano indicando se foi criada
    agora (True) ou se já existia (False).

    Regras:
      - Se `conversation_id` for fornecido, a conversa tem de já existir.
      - Se não for fornecido, cria-se uma conversa nova com o sistema de
        origem como primeiro participante.
    """
    async with conn.cursor() as cur:
        if conversation_id is not None:
            await cur.execute(
                "SELECT conversation_id FROM conversations WHERE conversation_id = %(id)s",
                {"id": conversation_id},
            )
            row = await cur.fetchone()
            if row is None:
                raise ValueError(f"Conversa {conversation_id} não existe.")
            criada = False
        else:
            await cur.execute(
                """
                INSERT INTO conversations (assunto, status_geral, metadata)
                VALUES (%(assunto)s, %(status)s, '{}'::jsonb)
                RETURNING conversation_id
                """,
                {"assunto": assunto, "status": status},
            )
            row = await cur.fetchone()
            conversation_id = row["conversation_id"]
            criada = True

        await _upsert_participant(
            cur,
            conversation_id=conversation_id,
            sistema=origem,
            ref_externa=ref_externa,
            status_local=status,
        )

    if criada:
        await record_audit(
            conn,
            conversation_id=conversation_id,
            sistema=origem,
            evento_tipo="conversa_criada",
            detalhe={"ref_externa": ref_externa, "status": status},
        )

    return conversation_id, criada


async def _upsert_participant(
    cur, *, conversation_id: UUID, sistema: str, ref_externa: str, status_local: str
) -> None:
    await cur.execute(
        """
        INSERT INTO conversation_participants
            (conversation_id, sistema, ref_externa, status_local)
        VALUES (%(cid)s, %(sistema)s, %(ref)s, %(status)s)
        ON CONFLICT (conversation_id, sistema)
        DO UPDATE SET
            ref_externa = EXCLUDED.ref_externa,
            status_local = EXCLUDED.status_local,
            updated_at = now()
        """,
        {"cid": conversation_id, "sistema": sistema, "ref": ref_externa, "status": status_local},
    )


async def get_participant_status(
    conn: AsyncConnection, *, conversation_id: UUID, sistema: str
) -> str | None:
    """Devolve o status_local atualmente guardado para (conversa, sistema), ou None se não existir."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT status_local FROM conversation_participants
            WHERE conversation_id = %(cid)s AND sistema = %(sistema)s
            """,
            {"cid": conversation_id, "sistema": sistema},
        )
        row = await cur.fetchone()
    return row["status_local"] if row else None


async def list_other_participants(
    conn: AsyncConnection, *, conversation_id: UUID, excluir: str
) -> list[dict[str, Any]]:
    """Lista os participantes de uma conversa exceto o sistema de origem - usado para o fan-out."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT sistema, ref_externa, status_local
            FROM conversation_participants
            WHERE conversation_id = %(cid)s AND sistema != %(excluir)s
            """,
            {"cid": conversation_id, "excluir": excluir},
        )
        return await cur.fetchall()


async def update_conversation_status_geral(
    conn: AsyncConnection, *, conversation_id: UUID, status_geral: str
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE conversations SET status_geral = %(status)s WHERE conversation_id = %(cid)s",
            {"status": status_geral, "cid": conversation_id},
        )
