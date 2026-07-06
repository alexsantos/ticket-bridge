"""
audit.py
--------
GET /api/v1/audit - consulta do registo de auditoria, com filtros opcionais
por conversa e por sistema. Alimenta o separador "Auditoria" do frontend.
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
    sistema: str | None = None,
) -> list[AuditLogOut]:
    async with get_connection() as conn:
        rows = await list_recent(
            conn, limit=limit, conversation_id=conversation_id, sistema=sistema
        )
    return [AuditLogOut(**row) for row in rows]
