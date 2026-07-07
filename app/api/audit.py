"""
audit.py
--------
GET /api/v1/audit - queries the audit trail page by page, with optional
filters by conversation and by system. Feeds the frontend's "Audit" tab.
"""
from uuid import UUID

from fastapi import APIRouter, Query

from app.database import get_connection
from app.schemas import AuditLogOut, AuditLogPage
from app.services.audit_service import list_recent

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=AuditLogPage)
async def get_audit_log(
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    conversation_id: UUID | None = None,
    system_code: str | None = None,
) -> AuditLogPage:
    async with get_connection() as conn:
        rows, has_more = await list_recent(
            conn, limit=limit, offset=offset, conversation_id=conversation_id, system_code=system_code
        )
    return AuditLogPage(
        items=[AuditLogOut(**row) for row in rows],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )
