"""
audit_service.py
-----------------
Writing to and reading from the audit trail (`audit_log`). It's append-only
by design - the application never issues UPDATE or DELETE against this
table. It's the basis for the frontend's "Audit" tab and the first place to
look when something goes wrong in an integration.
"""
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


async def record_audit(
    conn: AsyncConnection,
    *,
    event_type: str,
    conversation_id: UUID | None = None,
    system_code: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Writes an audit row. Should be called within the same transaction as the associated business event."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO audit_log (conversation_id, system_code, event_type, detail)
            VALUES (%(cid)s, %(system_code)s, %(type)s, %(detail)s)
            """,
            {
                "cid": conversation_id,
                "system_code": system_code,
                "type": event_type,
                "detail": detail or {},
            },
        )


async def list_recent(
    conn: AsyncConnection,
    *,
    limit: int = 100,
    offset: int = 0,
    conversation_id: UUID | None = None,
    system_code: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Lists audit entries page by page (most recent first), with optional
    filters. Returns (rows, has_more) - `has_more` is computed by fetching
    one extra row past `limit` rather than a separate COUNT(*) query.

    Sorted by `created_at DESC, id DESC`: multiple rows written in the same
    transaction (e.g. several delivery_success entries from one /sync
    batch) can share the exact same `created_at` timestamp, and `ORDER BY
    created_at DESC` alone is not a stable sort in that case - without the
    `id` tiebreaker, rows could be skipped or repeated across pages.
    """
    filters = []
    params: dict[str, Any] = {"limit": limit + 1, "offset": offset}

    if conversation_id is not None:
        filters.append("conversation_id = %(cid)s")
        params["cid"] = conversation_id
    if system_code is not None:
        filters.append("system_code = %(system_code)s")
        params["system_code"] = system_code

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT id, conversation_id, system_code, event_type, detail, created_at
            FROM audit_log
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT %(limit)s
            OFFSET %(offset)s
            """,
            params,
        )
        rows = await cur.fetchall()

    has_more = len(rows) > limit
    return rows[:limit], has_more
