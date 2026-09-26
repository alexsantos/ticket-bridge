"""
sync.py
-------
POST /api/v1/sync

Manual/on-demand trigger for outbox processing - useful for ops,
debugging, or forcing an immediate reprocessing without waiting for the
next tick. The primary trigger is now the in-process scheduler
(app/scheduler.py), which calls the same underlying
sync_service.run_sync_batch() automatically on a fixed interval - see
CLAUDE.md Decision 2. Callable by a logged-in admin (the frontend's
"Sync now" button, the example scripts), or by an external scheduler with
SCHEDULER_SHARED_SECRET if one is configured - see authorize_sync in
app/security.py.
"""
from fastapi import APIRouter, Depends

from app.schemas import SyncResult
from app.security import authorize_sync
from app.services.sync_service import run_sync_batch

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


@router.post("", response_model=SyncResult, dependencies=[Depends(authorize_sync)])
async def run_sync() -> SyncResult:
    return SyncResult(**await run_sync_batch())
