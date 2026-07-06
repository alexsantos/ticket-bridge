"""
scheduler.py
------------
In-process periodic trigger for outbox processing
(app/services/sync_service.py), so the service doesn't depend on an
external pinger like Google Cloud Scheduler. Suited to deployments where
the process runs continuously (a VM, a long-lived container) rather than
Cloud Run's scale-to-zero model - see CLAUDE.md Decision 2.

Uses APScheduler's AsyncIOScheduler, which runs the job on the same
asyncio event loop as the rest of the app - no extra thread or process.

Caveat: if this app is ever run as multiple concurrent instances/workers,
each one starts its own scheduler and fires independently. This is safe
(outbox_service.fetch_pending_batch's SELECT ... FOR UPDATE SKIP LOCKED
guarantees no double-processing) but means redundant polling queries
across instances. If that becomes wasteful, disable SYNC_SCHEDULER_ENABLED
everywhere except one instance, or fall back to a single external trigger
(e.g. Cloud Scheduler) calling POST /api/v1/sync instead.
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.services.sync_service import run_sync_batch

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def start_scheduler() -> None:
    """Starts the background sync job, if enabled. Called at application startup (lifespan)."""
    global _scheduler
    settings = get_settings()

    if not settings.sync_scheduler_enabled:
        logger.info("Internal sync scheduler disabled (SYNC_SCHEDULER_ENABLED=false).")
        return

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _run_sync_job,
        "interval",
        seconds=settings.sync_interval_seconds,
        id="outbox_sync",
        max_instances=1,  # never overlap a slow run with the next tick
        coalesce=True,  # if ticks are missed (e.g. a long GC pause), run once, not N times
    )
    _scheduler.start()
    logger.info("Internal sync scheduler started (every %ss).", settings.sync_interval_seconds)


def stop_scheduler() -> None:
    """Stops the background sync job, if it was started. Called at application shutdown (lifespan)."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Internal sync scheduler stopped.")


async def _run_sync_job() -> None:
    try:
        result = await run_sync_batch()
        if result["processed"]:
            logger.info("Scheduled sync run: %s", result)
    except Exception:
        logger.exception("Scheduled sync run failed unexpectedly.")
