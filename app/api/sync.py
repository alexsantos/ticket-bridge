"""
sync.py
-------
POST /api/v1/sync

Manual/on-demand trigger for outbox processing - useful for ops,
debugging, or forcing an immediate reprocessing without waiting for the
next tick. The primary trigger is now the in-process scheduler
(app/scheduler.py), which calls the same underlying
sync_service.run_sync_batch() automatically on a fixed interval - see
CLAUDE.md Decision 2. This endpoint is authenticated the same way
(X-Scheduler-Secret) regardless of who calls it.
"""
from fastapi import APIRouter, Depends

from app.schemas import SyncResult
from app.security import authenticate_scheduler
from app.services.sync_service import run_sync_batch

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


@router.post("", response_model=SyncResult, dependencies=[Depends(authenticate_scheduler)])
async def run_sync() -> SyncResult:
    return SyncResult(**await run_sync_batch())
