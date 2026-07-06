"""
outbox_service.py
------------------
Implementa o outbox pattern: em vez de um broker externo (RabbitMQ /
Pub-Sub), usamos a própria tabela `outbox` no Postgres como fila.

Porquê isto e não RabbitMQ (ver também CLAUDE.md):
  - Cloud Run escala a zero; um consumidor RabbitMQ tem de estar sempre
    vivo, o que contraria esse modelo.
  - A escrita do evento de negócio e a inserção na fila acontecem na MESMA
    transação Postgres - não há risco de "gravei a conversa mas perdi o
    evento" (dual-write problem).
  - `FOR UPDATE SKIP LOCKED` dá concorrência seguíssima entre invocações
    concorrentes do /sync sem precisar de coordenação externa.

Trade-off aceite: latência de minutos (cadência do Cloud Scheduler), não
segundos. Para sincronizar estado de tickets entre equipas, isto é adequado.
"""
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


async def enqueue(
    conn: AsyncConnection,
    *,
    conversation_id: UUID,
    destino: str,
    origem: str,
    payload: dict[str, Any],
) -> int:
    """Insere uma nova entrada pendente na outbox. Devolve o id criado."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO outbox (conversation_id, destino, origem, payload, status)
            VALUES (%(cid)s, %(destino)s, %(origem)s, %(payload)s, 'pending')
            RETURNING id
            """,
            {
                "cid": conversation_id,
                "destino": destino,
                "origem": origem,
                "payload": payload,
            },
        )
        row = await cur.fetchone()
    return row["id"]


async def fetch_pending_batch(conn: AsyncConnection, *, limit: int) -> list[dict[str, Any]]:
    """
    Reserva um lote de linhas pendentes para processamento nesta invocação,
    usando SKIP LOCKED para que invocações concorrentes do /sync nunca
    peguem na mesma linha.

    Nota: isto corre DENTRO de uma transação que o chamador deve manter
    aberta até marcar as linhas como sent/failed (ver dispatcher.py).
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, conversation_id, destino, origem, payload, tentativas, max_tentativas
            FROM outbox
            WHERE status = 'pending' AND tentativas < max_tentativas
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT %(limit)s
            """,
            {"limit": limit},
        )
        return await cur.fetchall()


async def mark_sent(conn: AsyncConnection, *, outbox_id: int) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE outbox
            SET status = 'sent', processed_at = now()
            WHERE id = %(id)s
            """,
            {"id": outbox_id},
        )


async def mark_failed(conn: AsyncConnection, *, outbox_id: int, erro: str) -> None:
    """
    Regista uma tentativa falhada. Se atingir max_tentativas, o status passa
    a 'failed' (para intervenção manual visível no frontend de auditoria);
    caso contrário mantém-se 'pending' para nova tentativa no próximo /sync.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE outbox
            SET tentativas = tentativas + 1,
                last_error = %(erro)s,
                status = CASE
                    WHEN tentativas + 1 >= max_tentativas THEN 'failed'
                    ELSE 'pending'
                END
            WHERE id = %(id)s
            """,
            {"id": outbox_id, "erro": erro[:2000]},
        )
