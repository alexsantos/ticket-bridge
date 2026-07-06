"""
main.py
-------
FastAPI application entry point.

Responsibilities:
  - Lifespan: opens/closes the database connection pool.
  - Registers each functional area's routers (app/api/*).
  - Serves the static configuration/audit frontend at "/".
  - Health check endpoint at "/health" (used by Cloud Run).

Run locally:
    uvicorn app.main:app --reload --port 8080

Run in production (Cloud Run):
    see Dockerfile - uses the same uvicorn command, without --reload.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import audit, conversations, events, sync, systems
from app.config import get_settings
from app.database import close_pool, init_pool

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    logger.info("Ticket Bridge started (environment=%s).", settings.environment)
    yield
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
app.include_router(conversations.router)
app.include_router(audit.router)


@app.get("/health", tags=["infra"])
async def health() -> dict:
    """Used by Cloud Run / external monitoring to check availability."""
    return {"status": "ok"}


# Static configuration and audit frontend (see app/frontend/).
_frontend_dir = Path(__file__).parent / "frontend"
app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
