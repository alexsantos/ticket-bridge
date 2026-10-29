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

    Rows for a pull system (systems.delivery_mode = 'pull') are skipped:
    that system collects them itself from its inbox (list_inbox below).

    Note: this runs INSIDE a transaction that the caller must keep open
    until the rows are marked sent/failed (see dispatcher.py).
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, conversation_id, destination, source, payload, attempts, max_attempts
            FROM outbox
            WHERE status = 'pending' AND attempts < max_attempts
              AND NOT EXISTS (
                  SELECT 1 FROM systems s WHERE s.code = outbox.destination AND s.delivery_mode = 'pull'
              )
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


async def list_entries(
    conn: AsyncConnection,
    *,
    status: str | None = None,
    destination: str | None = None,
    conversation_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Lists outbox entries page by page (most recent first), with optional
    filters - feeds the frontend's "Deliveries" tab. Returns (rows,
    has_more), computed the same way as audit_service.list_recent (one
    extra row past `limit`, no COUNT(*)).

    Each row carries the conversation's subject, the payload's `event`
    (ticket.created / ticket.updated) and the full payload - exactly what
    was (or will be) POSTed to the destination.
    """
    filters = []
    params: dict[str, Any] = {"limit": limit + 1, "offset": offset}

    if status is not None:
        filters.append("o.status = %(status)s")
        params["status"] = status
    if destination is not None:
        filters.append("o.destination = %(destination)s")
        params["destination"] = destination
    if conversation_id is not None:
        filters.append("o.conversation_id = %(conversation_id)s")
        params["conversation_id"] = conversation_id

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT o.id, o.conversation_id, c.subject AS conversation_subject,
                   o.destination, o.source, o.payload->>'event' AS event, o.payload,
                   o.status, o.attempts, o.max_attempts, o.last_error,
                   o.created_at, o.processed_at
            FROM outbox o
            JOIN conversations c ON c.conversation_id = o.conversation_id
            {where_clause}
            ORDER BY o.id DESC
            LIMIT %(limit)s
            OFFSET %(offset)s
            """,
            params,
        )
        rows = await cur.fetchall()

    has_more = len(rows) > limit
    return rows[:limit], has_more


async def requeue_failed(conn: AsyncConnection, *, outbox_ids: list[int]) -> list[dict[str, Any]]:
    """
    Puts the given 'failed' entries back in the queue ('pending', attempts
    reset to 0), so the next /sync delivers them again with the same
    payload. Entries that aren't 'failed' (pending ones are still being
    retried, sent ones were delivered) are left untouched and not returned.

    Returns one row per requeued entry, with its attempts and last_error
    from *before* the reset - the caller records them in the audit log,
    since the reset clears them from the outbox row itself.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            WITH previous AS (
                SELECT id, attempts, last_error
                FROM outbox
                WHERE id = ANY(%(ids)s) AND status = 'failed'
                FOR UPDATE
            )
            UPDATE outbox o
            SET status = 'pending', attempts = 0, last_error = NULL, processed_at = NULL
            FROM previous
            WHERE o.id = previous.id
            RETURNING o.id, o.conversation_id, o.destination,
                      previous.attempts AS previous_attempts, previous.last_error AS previous_error
            """,
            {"ids": outbox_ids},
        )
        return await cur.fetchall()


async def list_inbox(conn: AsyncConnection, *, destination: str, limit: int) -> tuple[list[dict[str, Any]], bool]:
    """
    A pull system's waiting messages: its pending outbox rows, oldest
    first. Returns (rows, has_more). Rows stay pending until acknowledged
    (ack_inbox), so a system that crashes mid-batch gets them again.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, payload, created_at FROM outbox
            WHERE destination = %(destination)s AND status = 'pending'
            ORDER BY id
            LIMIT %(limit)s
            """,
            {"destination": destination, "limit": limit + 1},
        )
        rows = await cur.fetchall()
    return rows[:limit], len(rows) > limit


async def ack_inbox(conn: AsyncConnection, *, destination: str, outbox_ids: list[int]) -> list[dict[str, Any]]:
    """
    Marks the given messages delivered ('sent') - only `destination`'s own
    pending rows; anything else is left alone and not returned.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE outbox SET status = 'sent', processed_at = now()
            WHERE id = ANY(%(ids)s) AND destination = %(destination)s AND status = 'pending'
            RETURNING id, conversation_id
            """,
            {"ids": outbox_ids, "destination": destination},
        )
        return await cur.fetchall()
