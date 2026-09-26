"""
sync_service.py
-----------------
Core outbox-processing logic: reserves a batch of pending entries and
attempts delivery to each destination system (see outbox_service.py for
the queue mechanics, dispatcher.py for the HTTP delivery itself).

Extracted as a plain service function - rather than living directly in
api/sync.py - so it can be called from two independent triggers without
either depending on FastAPI:
  1. POST /api/v1/sync (app/api/sync.py) - a manual/on-demand trigger,
     useful for ops, debugging, or ad-hoc reprocessing.
  2. The in-process scheduler (app/scheduler.py) - the primary trigger,
     firing on a fixed interval so the service doesn't depend on an
     external pinger like Google Cloud Scheduler. See CLAUDE.md Decision 2.

Each entry is processed in its own mini-transaction (reserved with
FOR UPDATE SKIP LOCKED, delivery attempted, marked sent/failed) so a
failure in one delivery doesn't block the rest of the batch.
"""
import logging

from app.config import get_settings
from app.database import get_connection
from app.services import outbox_service, secret_store
from app.services.audit_service import record_audit
from app.services.dispatcher import DeliveryError, deliver

logger = logging.getLogger(__name__)


async def run_sync_batch() -> dict:
    """Processes one batch of pending outbox entries. Returns {processed, success, failures, detail}."""
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

                try:
                    await deliver(
                        base_url=destination_system["base_url"],
                        auth_config=destination_system["auth_config"],
                        payload=entry["payload"],
                        resolved_secret=resolve_outbound_secret(destination_system),
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
    return {"processed": processed, "success": success, "failures": failures, "detail": detail}


def resolve_outbound_secret(system: dict) -> str | None:
    """
    The secret to send to `system`, or None if it has none.

    The only source is the secret stored (encrypted) from the frontend. A
    system still carrying an `auth_config.secret_ref` from before 0.8.0
    (CLAUDE.md Decision 14) expected authentication that can no longer be
    looked up, so it fails the delivery - marked failed, retried, reason in
    the audit log - rather than being delivered without the header. The
    same goes for a stored secret that can't be decrypted.
    """
    if system.get("outbound_secret_encrypted"):
        try:
            return secret_store.decrypt(system["outbound_secret_encrypted"])
        except secret_store.SecretStoreError as exc:
            raise DeliveryError(str(exc)) from exc

    if (system.get("auth_config") or {}).get("secret_ref"):
        raise DeliveryError(
            "This system still uses a secret reference (secret_ref), which is no longer "
            "supported - set its outbound secret in the Systems tab, or mark it as having "
            "no secret. Not delivering without auth."
        )
    return None


async def _get_system_config(conn, code: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT code, base_url, auth_config, outbound_secret_encrypted, active "
            "FROM systems WHERE code = %(code)s",
            {"code": code},
        )
        return await cur.fetchone()
