"""
security.py
-----------
Authentication of inbound calls:

  1. POST /api/v1/events - authenticated with a per-system API key
     (api_keys table), sent in the `X-API-Key` header.
  2. POST /api/v1/sync - authenticated with a simple shared secret
     (SCHEDULER_SHARED_SECRET), sent in the `X-Scheduler-Secret` header by
     whoever manually triggers a sync run (ops, a script, or - in a Cloud
     Run deployment without the in-process scheduler - Cloud Scheduler; in
     production on Cloud Run, prefer its native OIDC instead, see
     README.md "Sync endpoint security" section). This has nothing to do
     with the in-process scheduler (app/scheduler.py), which calls the
     same sync logic directly in-process and needs no authentication.
  3. Configuration/audit endpoints (/api/v1/systems, /api/v1/topics,
     /api/v1/conversations, /api/v1/audit) - authenticated with a human
     admin session: an httpOnly cookie validated against the `sessions`
     table (`require_login` below). See app/api/auth.py. This used to be
     left to an external authentication proxy (Cloud Run IAM / IAP) - see
     CLAUDE.md Decision 11 for why that's no longer assumed.

We never store API keys, session tokens, or passwords in plaintext - API
keys and session tokens only as a SHA-256 hash, passwords only as a
bcrypt hash.
"""
import hashlib
import hmac
import secrets

import bcrypt
from fastapi import Cookie, Header, HTTPException, status

from app.config import get_settings
from app.database import get_connection


def hash_key(raw_key: str) -> str:
    """Computes the SHA-256 hash of a plaintext key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """
    Generates a new inbound API key: (plaintext, sha256_hash). Only the
    hash is meant to be persisted (api_keys.key_hash) - the plaintext is
    returned to the caller once, at creation time, and is never stored or
    retrievable again.
    """
    raw_key = secrets.token_urlsafe(32)
    return raw_key, hash_key(raw_key)


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


def hash_password(raw_password: str) -> str:
    """Computes a bcrypt hash of a plaintext password, for storage in users.password_hash."""
    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw_password: str, password_hash: str) -> bool:
    """Checks a plaintext password against a stored bcrypt hash."""
    return bcrypt.checkpw(raw_password.encode("utf-8"), password_hash.encode("utf-8"))


def generate_session_token() -> tuple[str, str]:
    """
    Generates a new admin session token: (plaintext, sha256_hash) - mirrors
    generate_api_key(). The plaintext is only ever held by the browser (as
    the httpOnly session cookie's value, set once at login in
    app/api/auth.py); only the hash is persisted (sessions.token_hash).
    """
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_key(raw_token)


async def require_login(
    session_token: str | None = Cookie(default=None, alias=get_settings().session_cookie_name)
) -> str:
    """
    FastAPI dependency: validates the admin session cookie and returns the
    authenticated username, or raises 401. Applied at the router level
    (dependencies=[Depends(require_login)] on each APIRouter(...)) to
    systems.py/topics.py/conversations.py/audit.py, mirroring how sync.py
    applies authenticate_scheduler to every route under it.
    """
    if session_token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")

    token_hash = hash_key(session_token)
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT u.username FROM sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = %(hash)s AND s.active = TRUE
                  AND s.expires_at > now() AND u.active = TRUE
                """,
                {"hash": token_hash},
            )
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid.")
    return row["username"]
