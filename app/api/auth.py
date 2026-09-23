"""
auth.py
-------
Human authentication for the admin/config frontend: login, logout,
"who am I", and self-service password change. Distinct from
authenticate_system (per-system API key, /api/v1/events) and
authenticate_scheduler (shared secret, /api/v1/sync) - see app/security.py.

Sessions are DB-backed (`sessions` table) and transported as an httpOnly
cookie, so every existing fetch() call in app.js keeps working unmodified
(the browser sends the cookie automatically) - no bearer-token header to
thread through every call site. See CLAUDE.md Decision 11.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status

from app.config import get_settings
from app.database import get_connection
from app.schemas import ChangePasswordRequest, LoginRequest, UserOut
from app.security import generate_session_token, hash_key, hash_password, require_login, verify_password
from app.services.audit_service import record_audit

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


async def _recent_failed_logins(conn, username: str) -> int:
    """
    Counts failed logins for `username` within the lockout window, ignoring
    any before its most recent successful login (a success resets the
    count). Backed by audit_log rather than a dedicated table - failures
    are already worth auditing, and the volume is tiny.
    """
    settings = get_settings()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT count(*) AS failures FROM audit_log
            WHERE event_type = 'admin_login_failed'
              AND detail->>'username' = %(u)s
              AND created_at > GREATEST(
                  now() - make_interval(mins => %(mins)s),
                  COALESCE(
                      (SELECT max(created_at) FROM audit_log
                       WHERE event_type = 'admin_login' AND detail->>'username' = %(u)s),
                      '-infinity'
                  )
              )
            """,
            {"u": username, "mins": settings.login_lockout_minutes},
        )
        row = await cur.fetchone()
    return row["failures"]


@router.post("/login", response_model=UserOut)
async def login(payload: LoginRequest, response: Response) -> UserOut:
    settings = get_settings()
    async with get_connection() as conn:
        # Checked before any bcrypt work, and for unknown usernames too, so a
        # throttled attacker gets the same 429 regardless of whether the
        # account exists.
        if await _recent_failed_logins(conn, payload.username) >= settings.login_max_failed_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Try again later.",
                headers={"Retry-After": str(settings.login_lockout_minutes * 60)},
            )

        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, password_hash FROM users WHERE username = %(username)s AND active = TRUE",
                {"username": payload.username},
            )
            row = await cur.fetchone()

        if row is None or not verify_password(payload.password, row["password_hash"]):
            await record_audit(
                conn, system_code=None, event_type="admin_login_failed", detail={"username": payload.username}
            )
            # Explicit commit: the pool rolls back on exception, which would
            # otherwise discard this row along with the 401 below.
            await conn.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")

        raw_token, token_hash = generate_session_token()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_lifetime_hours)

        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%(uid)s, %(hash)s, %(exp)s)",
                    {"uid": row["id"], "hash": token_hash, "exp": expires_at},
                )
            await record_audit(
                conn, system_code=None, event_type="admin_login", detail={"username": payload.username}
            )

    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        samesite="lax",
        secure=settings.environment != "local",
        path="/",
        max_age=settings.session_lifetime_hours * 3600,
    )
    return UserOut(username=payload.username)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    session_token: str | None = Cookie(default=None, alias=get_settings().session_cookie_name),
) -> None:
    """
    Best-effort revoke: not gated by require_login, so calling this with no
    (or an already-expired/invalid) cookie is a harmless no-op rather than
    a 401 - matches typical logout UX.
    """
    settings = get_settings()
    if session_token:
        async with get_connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        UPDATE sessions SET active = FALSE, revoked_at = now()
                        WHERE token_hash = %(hash)s AND active = TRUE
                        """,
                        {"hash": hash_key(session_token)},
                    )
    response.delete_cookie(key=settings.session_cookie_name, path="/")


@router.get("/me", response_model=UserOut)
async def me(username: str = Depends(require_login)) -> UserOut:
    """Used by the frontend on page load to decide whether to redirect to login.html."""
    return UserOut(username=username)


@router.post("/change-password", status_code=204)
async def change_password(
    payload: ChangePasswordRequest,
    username: str = Depends(require_login),
    session_token: str = Cookie(alias=get_settings().session_cookie_name),
) -> None:
    """
    Also revokes every *other* active session of this user, so a leaked or
    forgotten session doesn't outlive the password it was opened with. The
    caller's own session (the cookie on this request) stays valid.

    A wrong current password is a 400, not a 401: the session itself is
    fine, and app.js treats any 401 as "session expired, go to login".
    """
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, password_hash FROM users WHERE username = %(u)s", {"u": username})
            row = await cur.fetchone()

        if row is None or not verify_password(payload.current_password, row["password_hash"]):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect.")

        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE users SET password_hash = %(h)s WHERE id = %(id)s",
                    {"h": hash_password(payload.new_password), "id": row["id"]},
                )
                await cur.execute(
                    """
                    UPDATE sessions SET active = FALSE, revoked_at = now()
                    WHERE user_id = %(id)s AND active = TRUE AND token_hash <> %(current)s
                    """,
                    {"id": row["id"], "current": hash_key(session_token)},
                )
            await record_audit(
                conn, system_code=None, event_type="admin_password_changed", detail={"username": username}
            )
