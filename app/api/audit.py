"""
audit.py
--------
GET /api/v1/audit - queries the audit trail, with optional filters by
conversation and by system. Feeds the frontend's "Audit" tab.
"""
from uuid import UUID

from fastapi import APIRouter, Query

from app.database import get_connection
from app.schemas import AuditLogOut
from app.services.audit_service import list_recent

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogOut])
async def get_audit_log(
    limit: int = Query(default=100, le=500),
    conversation_id: UUID | None = None,
    system_code: str | None = None,
) -> list[AuditLogOut]:
    async with get_connection() as conn:
        rows = await list_recent(
            conn, limit=limit, conversation_id=conversation_id, system_code=system_code
        )
    return [AuditLogOut(**row) for row in rows]
