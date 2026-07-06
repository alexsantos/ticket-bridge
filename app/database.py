"""
database.py
-----------
Management of the asynchronous PostgreSQL connection pool (via psycopg3 +
pool).

We deliberately keep database interaction as explicit SQL (no heavy ORM):
the table count is small, the concurrency logic (FOR UPDATE SKIP LOCKED)
needs fine-grained control, and an ORM would add complexity without real
benefit in this context.
"""
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import psycopg
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from psycopg.types.json import JsonbBinaryDumper, JsonbDumper

from app.config import get_settings

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None

# psycopg3 doesn't adapt plain dicts to jsonb by default (unlike psycopg2) -
# every jsonb column in this schema (systems.auth_config/status_mapping/
# payload_template, conversations.metadata, outbox.payload, audit_log.detail)
# is written from a plain Python dict throughout the codebase, so register
# this once globally instead of wrapping every call site in Jsonb(...).
psycopg.adapters.register_dumper(dict, JsonbDumper)
psycopg.adapters.register_dumper(dict, JsonbBinaryDumper)


async def init_pool() -> None:
    """Initializes the connection pool. Called at application startup (lifespan)."""
    global _pool
    settings = get_settings()
    _pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        kwargs={"row_factory": dict_row, "autocommit": False},
        open=False,
    )
    await _pool.open(wait=True, timeout=15)
    logger.info("Database connection pool initialized.")


async def close_pool() -> None:
    """Closes the connection pool. Called at application shutdown (lifespan)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        logger.info("Database connection pool closed.")


@asynccontextmanager
async def get_connection() -> AsyncIterator:
    """
    Context manager to obtain a connection from the pool within an
    endpoint/service.

    Typical usage:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT ...")
    """
    if _pool is None:
        raise RuntimeError("Connection pool not initialized - init_pool() was not called.")
    async with _pool.connection() as conn:
        yield conn
