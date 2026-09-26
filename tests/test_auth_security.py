"""
test_auth_security.py
----------------------
Unit tests for the admin-authentication helpers in app/security.py:
password hashing (bcrypt), session token generation, and the parts of
require_login that don't need a live database (the no-cookie -> 401 path).

The DB-lookup branch of require_login, and the full login -> me -> logout
round trip, need a real Postgres connection and are not covered here -
this project has no DB-backed pytest tests (see tests/test_dispatcher.py,
tests/test_outbound_secrets.py); see README.md's "Verifying auth locally"
checklist for that manual verification instead.

    pytest tests/test_auth_security.py -v
"""
import pytest
from fastapi import HTTPException

from app.security import generate_session_token, hash_key, hash_password, require_login, verify_password


def test_hash_password_and_verify_roundtrip():
    password_hash = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", password_hash) is True
    assert verify_password("wrong password", password_hash) is False


def test_hash_password_uses_a_random_salt_per_call():
    """bcrypt salts each call independently - two hashes of the same password must differ,
    but both must still verify against that same password."""
    first_hash = hash_password("same-password")
    second_hash = hash_password("same-password")

    assert first_hash != second_hash
    assert verify_password("same-password", first_hash) is True
    assert verify_password("same-password", second_hash) is True


def test_generate_session_token_hash_matches_hash_key():
    """generate_session_token mirrors generate_api_key's contract: the returned hash is
    exactly hash_key(plaintext), so require_login's lookup (which hashes the cookie value
    the same way) matches what was stored at login time."""
    raw_token, token_hash = generate_session_token()

    assert token_hash == hash_key(raw_token)
    assert raw_token != token_hash


@pytest.mark.asyncio
async def test_require_login_rejects_missing_cookie():
    """No cookie -> 401 before ever touching the database (this branch returns early)."""
    with pytest.raises(HTTPException) as exc_info:
        await require_login(session_token=None)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_login_throttled_after_max_failed_attempts(monkeypatch):
    """Once the failure count reaches LOGIN_MAX_FAILED_ATTEMPTS, login returns 429
    before looking the user up or running bcrypt - even for a correct password."""
    from contextlib import asynccontextmanager

    from fastapi import Response

    from app.api import auth
    from app.config import get_settings
    from app.schemas import LoginRequest

    @asynccontextmanager
    async def fake_connection():
        yield object()  # never used for SQL: _recent_failed_logins is stubbed below

    async def fake_recent_failed_logins(conn, username):
        return get_settings().login_max_failed_attempts

    def fail_if_called(*args, **kwargs):
        raise AssertionError("bcrypt must not run for a throttled username")

    monkeypatch.setattr(auth, "get_connection", fake_connection)
    monkeypatch.setattr(auth, "_recent_failed_logins", fake_recent_failed_logins)
    monkeypatch.setattr(auth, "verify_password", fail_if_called)

    with pytest.raises(HTTPException) as exc_info:
        await auth.login(LoginRequest(username="admin", password="whatever"), Response())

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == str(get_settings().login_lockout_minutes * 60)


# ---------------------------------------------------------------------------
# POST /api/v1/sync authorization (authorize_sync) - CLAUDE.md Decision 14
# ---------------------------------------------------------------------------
def _scheduler_secret(monkeypatch, value: str):
    from types import SimpleNamespace

    import app.security as security
    monkeypatch.setattr(security, "get_settings", lambda: SimpleNamespace(scheduler_shared_secret=value))


@pytest.mark.asyncio
async def test_sync_accepts_configured_scheduler_secret(monkeypatch):
    from app.security import authorize_sync
    _scheduler_secret(monkeypatch, "s3cret")

    assert await authorize_sync(x_scheduler_secret="s3cret", session_token=None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("configured, presented", [
    ("s3cret", "wrong"),
    ("", ""),                          # unset secret must not be matchable by an empty header...
    ("", "change-me-in-production"),   # ...nor by the old default value
])
async def test_sync_rejects_bad_or_unconfigured_scheduler_secret(monkeypatch, configured, presented):
    from app.security import authorize_sync
    _scheduler_secret(monkeypatch, configured)

    with pytest.raises(HTTPException) as exc_info:
        await authorize_sync(x_scheduler_secret=presented, session_token=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_sync_without_header_requires_admin_session(monkeypatch):
    """No header -> falls through to require_login, which rejects a missing cookie before touching the DB."""
    from app.security import authorize_sync
    _scheduler_secret(monkeypatch, "s3cret")

    with pytest.raises(HTTPException) as exc_info:
        await authorize_sync(x_scheduler_secret=None, session_token=None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Not authenticated."
