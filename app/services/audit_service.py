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
    conversation_id: UUID | None = None,
    system_code: str | None = None,
) -> list[dict[str, Any]]:
    """Lists the most recent audit entries, with optional filters."""
    filters = []
    params: dict[str, Any] = {"limit": limit}

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
            ORDER BY created_at DESC
            LIMIT %(limit)s
            """,
            params,
        )
        return await cur.fetchall()
