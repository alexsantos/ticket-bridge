"""
inbox.py
--------
GET  /api/v1/inbox       - a pull system's waiting messages, oldest first.
POST /api/v1/inbox/ack   - marks the ones it has processed as delivered.

For a system that can't expose a webhook the bridge can reach (e.g. a
cloud product that only makes outbound calls): with
systems.delivery_mode = 'pull', the bridge never calls it, and it
collects its messages here instead - each one exactly the
OutboundTicketEvent a push system receives, wrapped with the delivery_id
to acknowledge. See CLAUDE.md Decision 18.

Delivery is at-least-once: a message stays waiting until acknowledged,
so one the system fetched but never acknowledged (it crashed, timed out)
comes back on the next call. Systems must ignore a delivery_id they've
already handled.

Authenticated with the system's own inbound API key (X-API-Key), the same
one it uses for POST /api/v1/events - never an admin session. Every call
records systems.last_polled_at, so the admin panel can warn when a pull
system stops collecting.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import get_connection
from app.schemas import InboxAck, InboxAckResult, InboxMessage, InboxPage
from app.security import authenticate_system
from app.services import outbox_service
from app.services.audit_service import record_audit

router = APIRouter(prefix="/api/v1/inbox", tags=["inbox"])


async def _pull_system(conn, code: str) -> None:
    """Records the poll, and refuses a push system - its messages go to its webhook, not here."""
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE systems SET last_polled_at = now() WHERE code = %(code)s AND delivery_mode = 'pull' RETURNING code",
            {"code": code},
        )
        if await cur.fetchone() is None:
            raise HTTPException(
                status_code=409,
                detail=f"System '{code}' receives its messages by webhook. Ask an admin to switch it "
                       "to collecting them (delivery mode 'pull') to use the inbox.",
            )


@router.get("", response_model=InboxPage)
async def read_inbox(
    limit: int = Query(default=50, ge=1, le=500),
    source: str = Depends(authenticate_system),
) -> InboxPage:
    async with get_connection() as conn:
        async with conn.transaction():
            await _pull_system(conn, source)
            rows, has_more = await outbox_service.list_inbox(conn, destination=source, limit=limit)
    return InboxPage(
        items=[InboxMessage(delivery_id=r["id"], queued_at=r["created_at"], message=r["payload"]) for r in rows],
        has_more=has_more,
    )


@router.post("/ack", response_model=InboxAckResult)
async def acknowledge(payload: InboxAck, source: str = Depends(authenticate_system)) -> InboxAckResult:
    requested = list(dict.fromkeys(payload.delivery_ids))
    async with get_connection() as conn:
        async with conn.transaction():
            await _pull_system(conn, source)
            acknowledged = await outbox_service.ack_inbox(conn, destination=source, outbox_ids=requested)
            for row in acknowledged:
                await record_audit(
                    conn,
                    conversation_id=row["conversation_id"],
                    system_code=source,
                    event_type="delivery_success",
                    detail={"outbox_id": row["id"], "collected": True},
                )
    done = {row["id"] for row in acknowledged}
    return InboxAckResult(
        acknowledged=[i for i in requested if i in done],
        skipped=[i for i in requested if i not in done],
    )
