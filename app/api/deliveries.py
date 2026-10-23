"""
deliveries.py
-------------
GET  /api/v1/deliveries        - lists outbox entries page by page, with
                                 optional filters by status and destination.
POST /api/v1/deliveries/retry  - puts the chosen 'failed' entries back in
                                 the queue.

Feeds the frontend's "Deliveries" tab: an entry that used up its
max_attempts stays 'failed' until an admin decides to retry it - e.g.
after fixing that system's configuration (URL, secret, verify_tls). Only
the entries the admin picks are retried, never "every failed entry" -
some may be stale or no longer wanted. A retry resends the payload exactly
as it was queued (outbox_service.requeue_failed).

Security note: every route here requires an authenticated admin session
(see require_login in app/security.py).
"""
from fastapi import APIRouter, Depends, Query

from app.database import get_connection
from app.schemas import OutboxEntryOut, OutboxPage, OutboxStatus, RetryDeliveriesRequest, RetryDeliveriesResult
from app.security import require_login
from app.services import outbox_service
from app.services.audit_service import record_audit

router = APIRouter(prefix="/api/v1/deliveries", tags=["deliveries"], dependencies=[Depends(require_login)])


@router.get("", response_model=OutboxPage)
async def list_deliveries(
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    status: OutboxStatus | None = None,
    system_code: str | None = Query(default=None, description="Destination system."),
) -> OutboxPage:
    async with get_connection() as conn:
        rows, has_more = await outbox_service.list_entries(
            conn, status=status, destination=system_code, limit=limit, offset=offset
        )
    return OutboxPage(
        items=[OutboxEntryOut(**row) for row in rows],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


@router.post("/retry", response_model=RetryDeliveriesResult)
async def retry_deliveries(payload: RetryDeliveriesRequest) -> RetryDeliveriesResult:
    requested = list(dict.fromkeys(payload.outbox_ids))
    async with get_connection() as conn:
        async with conn.transaction():
            requeued = await outbox_service.requeue_failed(conn, outbox_ids=requested)
            for row in requeued:
                await record_audit(
                    conn,
                    conversation_id=row["conversation_id"],
                    system_code=row["destination"],
                    event_type="delivery_retry_requested",
                    detail={
                        "outbox_id": row["id"],
                        "previous_attempts": row["previous_attempts"],
                        "previous_error": row["previous_error"],
                    },
                )
    retried = {row["id"] for row in requeued}
    return RetryDeliveriesResult(
        retried=[i for i in requested if i in retried],
        skipped=[i for i in requested if i not in retried],
    )
