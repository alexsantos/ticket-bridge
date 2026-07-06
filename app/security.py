"""
security.py
-----------
Authentication of inbound calls:

  1. POST /api/v1/events - authenticated with a per-system API key
     (api_keys table), sent in the `X-API-Key` header.
  2. POST /api/v1/sync - authenticated with a simple shared secret
     (SCHEDULER_SHARED_SECRET), sent by Cloud Scheduler in the
     `X-Scheduler-Secret` header. In production, prefer Cloud Scheduler's
     native OIDC + Cloud Run (see README.md, "Sync endpoint security"
     section).
  3. Configuration/audit endpoints (/api/v1/systems, /api/v1/conversations,
     /api/v1/audit) - protected by Cloud Run IAM (--no-allow-unauthenticated)
     or by an authentication proxy in front (see README.md).

We never store API keys in plaintext - only the SHA-256 hash.
"""
import hashlib
import hmac

from fastapi import Header, HTTPException, status

from app.config import get_settings
from app.database import get_connection


def hash_key(raw_key: str) -> str:
    """Computes the SHA-256 hash of a plaintext key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def authenticate_system(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """
    FastAPI dependency: validates the API key sent by an external system
    and returns the authenticated system's code (to use as `source` in the
    recorded events).
    """
    key_hash = hash_key(x_api_key)
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT system_code FROM api_keys
                WHERE key_hash = %(hash)s AND active = TRUE
                """,
                {"hash": key_hash},
            )
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key.",
        )
    return row["system_code"]


async def authenticate_scheduler(
    x_scheduler_secret: str = Header(..., alias="X-Scheduler-Secret")
) -> None:
    """FastAPI dependency: validates Cloud Scheduler's shared secret."""
    settings = get_settings()
    if not hmac.compare_digest(x_scheduler_secret, settings.scheduler_shared_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid scheduler secret.",
        )
