"""
sync.py
-------
POST /api/v1/sync

Called periodically by Cloud Scheduler (e.g. every 1-2 minutes). Processes
a batch of pending outbox entries, delivering each one to its destination
system via dispatcher.deliver().

Each row is processed in its own mini-transaction (reserved with
FOR UPDATE SKIP LOCKED, delivery attempted, marked sent/failed) so that a
failure in one delivery doesn't block the rest of the batch.
"""
import logging

from fastapi import APIRouter, Depends

from app.config import get_settings
from app.database import get_connection
from app.schemas import SyncResult
from app.security import authenticate_scheduler
from app.services import outbox_service
from app.services.audit_service import record_audit
from app.services.dispatcher import DeliveryError, deliver
from app.services.secrets import resolve_secret

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


@router.post("", response_model=SyncResult, dependencies=[Depends(authenticate_scheduler)])
async def run_sync() -> SyncResult:
    settings = get_settings()
    success = 0
    failures = 0
    detail = []

    async with get_connection() as conn:
        async with conn.transaction():
            batch = await outbox_service.fetch_pending_batch(conn, limit=settings.sync_batch_size)

            for entry in batch:
                destination_system = await _get_system_config(conn, entry["destination"])

                if destination_system is None or not destination_system["active"]:
                    await outbox_service.mark_failed(
                        conn, outbox_id=entry["id"], error="Destination system inactive or nonexistent."
                    )
                    failures += 1
                    detail.append({"outbox_id": entry["id"], "result": "inactive_system_failure"})
                    continue

                secret_ref = destination_system["auth_config"].get("secret_ref")
                resolved_secret = resolve_secret(secret_ref) if secret_ref else None

                try:
                    await deliver(
                        base_url=destination_system["base_url"],
                        auth_type=destination_system["auth_type"],
                        auth_config=destination_system["auth_config"],
                        payload=entry["payload"],
                        resolved_secret=resolved_secret,
                    )
                except DeliveryError as exc:
                    await outbox_service.mark_failed(conn, outbox_id=entry["id"], error=str(exc))
                    await record_audit(
                        conn,
                        conversation_id=entry["conversation_id"],
                        system_code=entry["destination"],
                        event_type="delivery_failure",
                        detail={"outbox_id": entry["id"], "error": str(exc)},
                    )
                    failures += 1
                    detail.append({"outbox_id": entry["id"], "result": "failure", "error": str(exc)})
                else:
                    await outbox_service.mark_sent(conn, outbox_id=entry["id"])
                    await record_audit(
                        conn,
                        conversation_id=entry["conversation_id"],
                        system_code=entry["destination"],
                        event_type="delivery_success",
                        detail={"outbox_id": entry["id"]},
                    )
                    success += 1
                    detail.append({"outbox_id": entry["id"], "result": "success"})

    processed = success + failures
    logger.info("Sync finished: %d processed, %d success, %d failures.", processed, success, failures)
    return SyncResult(processed=processed, success=success, failures=failures, detail=detail)


async def _get_system_config(conn, code: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT code, base_url, auth_type, auth_config, active FROM systems WHERE code = %(code)s",
            {"code": code},
        )
        return await cur.fetchone()
