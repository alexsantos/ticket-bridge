"""
outbox_service.py
------------------
Implements the outbox pattern: instead of an external broker (RabbitMQ /
Pub-Sub), we use the `outbox` table itself in Postgres as the queue.

Why this and not RabbitMQ (see also CLAUDE.md):
  - Cloud Run scales to zero; a RabbitMQ consumer needs to be always
    running, which conflicts with that model.
  - Writing the business event and inserting it into the queue happen in
    the SAME Postgres transaction - there's no risk of "I saved the
    conversation but lost the event" (the dual-write problem).
  - `FOR UPDATE SKIP LOCKED` gives safe concurrency across concurrent
    /sync invocations without needing external coordination.

Accepted trade-off: latency in the order of minutes (Cloud Scheduler's
cadence), not seconds. For synchronizing ticket state between teams, this
is adequate.
"""
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


async def enqueue(
    conn: AsyncConnection,
    *,
    conversation_id: UUID,
    destination: str,
    source: str,
    payload: dict[str, Any],
) -> int:
    """Inserts a new pending entry into the outbox. Returns the created id."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO outbox (conversation_id, destination, source, payload, status)
            VALUES (%(cid)s, %(destination)s, %(source)s, %(payload)s, 'pending')
            RETURNING id
            """,
            {
                "cid": conversation_id,
                "destination": destination,
                "source": source,
                "payload": payload,
            },
        )
        row = await cur.fetchone()
    return row["id"]


async def fetch_pending_batch(conn: AsyncConnection, *, limit: int) -> list[dict[str, Any]]:
    """
    Reserves a batch of pending rows for processing in this invocation,
    using SKIP LOCKED so that concurrent /sync invocations never grab the
    same row.

    Note: this runs INSIDE a transaction that the caller must keep open
    until the rows are marked sent/failed (see dispatcher.py).
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, conversation_id, destination, source, payload, attempts, max_attempts
            FROM outbox
            WHERE status = 'pending' AND attempts < max_attempts
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


async def mark_failed(conn: AsyncConnection, *, outbox_id: int, error: str) -> None:
    """
    Records a failed attempt. If max_attempts is reached, the status
    becomes 'failed' (for manual intervention, visible in the audit
    frontend); otherwise it stays 'pending' for another attempt on the next
    /sync.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE outbox
            SET attempts = attempts + 1,
                last_error = %(error)s,
                status = CASE
                    WHEN attempts + 1 >= max_attempts THEN 'failed'
                    ELSE 'pending'
                END
            WHERE id = %(id)s
            """,
            {"id": outbox_id, "error": error[:2000]},
        )
