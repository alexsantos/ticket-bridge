"""
main.py
-------
FastAPI application entry point.

Responsibilities:
  - Lifespan: opens/closes the database connection pool and starts/stops
    the in-process outbox-sync scheduler (app/scheduler.py).
  - Registers each functional area's routers (app/api/*).
  - Serves the static configuration/audit frontend at "/".
  - Health check endpoint at "/health" (used by Cloud Run / any container
    orchestrator's liveness probe).

Run locally:
    uvicorn app.main:app --reload --port 8080

Run in production (a long-lived container/VM, or Cloud Run):
    see Dockerfile - uses the same uvicorn command, without --reload.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import audit, conversations, events, sync, systems, topics
from app.config import get_settings
from app.database import close_pool, init_pool
from app.scheduler import start_scheduler, stop_scheduler

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    start_scheduler()
    logger.info("Ticket Bridge started (environment=%s).", settings.environment)
    yield
    stop_scheduler()
    await close_pool()
    logger.info("Ticket Bridge shut down.")


app = FastAPI(
    title="Ticket Bridge",
    description="Ticket correlation service across multiple support applications.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(events.router)
app.include_router(sync.router)
app.include_router(systems.router)
app.include_router(topics.router)
app.include_router(conversations.router)
app.include_router(audit.router)


@app.get("/health", tags=["infra"])
async def health() -> dict:
    """Used by Cloud Run / external monitoring to check availability."""
    return {"status": "ok"}


class RevalidatingStaticFiles(StaticFiles):
    """
    Adds `Cache-Control: no-cache` to every static frontend response, so
    browsers always revalidate (via the ETag/Last-Modified StaticFiles
    already sets) instead of blindly reusing a stale app.js/style.css.

    This project has no frontend build step (CLAUDE.md Decision 7), so
    there's no filename hashing to bust caches automatically on deploy -
    without this, a browser can keep running old JS against a newer API
    indefinitely (this is exactly what happened investigating a "the Audit
    tab shows no values" report: the API had changed shape, but the
    browser never re-fetched app.js to find out).
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["cache-control"] = "no-cache"
        return response


# Static configuration and audit frontend (see app/frontend/).
_frontend_dir = Path(__file__).parent / "frontend"
app.mount("/", RevalidatingStaticFiles(directory=_frontend_dir, html=True), name="frontend")
