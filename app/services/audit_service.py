"""
audit_service.py
-----------------
Escrita e leitura do registo de auditoria (`audit_log`). É append-only por
desenho - nunca há UPDATE nem DELETE sobre esta tabela a partir da aplicação.
É a base do separador "Auditoria" do frontend e o primeiro sítio a olhar
quando algo corre mal numa integração.
"""
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


async def record_audit(
    conn: AsyncConnection,
    *,
    evento_tipo: str,
    conversation_id: UUID | None = None,
    sistema: str | None = None,
    detalhe: dict[str, Any] | None = None,
) -> None:
    """Grava uma linha de auditoria. Deve ser chamado dentro da mesma transação do evento de negócio associado."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO audit_log (conversation_id, sistema, evento_tipo, detalhe)
            VALUES (%(cid)s, %(sistema)s, %(tipo)s, %(detalhe)s)
            """,
            {
                "cid": conversation_id,
                "sistema": sistema,
                "tipo": evento_tipo,
                "detalhe": detalhe or {},
            },
        )


async def list_recent(
    conn: AsyncConnection,
    *,
    limit: int = 100,
    conversation_id: UUID | None = None,
    sistema: str | None = None,
) -> list[dict[str, Any]]:
    """Lista as entradas de auditoria mais recentes, com filtros opcionais."""
    filtros = []
    params: dict[str, Any] = {"limit": limit}

    if conversation_id is not None:
        filtros.append("conversation_id = %(cid)s")
        params["cid"] = conversation_id
    if sistema is not None:
        filtros.append("sistema = %(sistema)s")
        params["sistema"] = sistema

    where_clause = f"WHERE {' AND '.join(filtros)}" if filtros else ""

    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT id, conversation_id, sistema, evento_tipo, detalhe, created_at
            FROM audit_log
            {where_clause}
            ORDER BY created_at DESC
            LIMIT %(limit)s
            """,
            params,
        )
        return await cur.fetchall()
