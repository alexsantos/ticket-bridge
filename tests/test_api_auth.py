"""
test_api_auth.py
-----------------
Admin authentication against a real database (Decision 11): login and
its audit trail, throttling counted from audit_log, logout revoking the
session, and change-password revoking every *other* session.

    pytest tests/test_api_auth.py -v
"""
import httpx

from app.config import get_settings
from tests.helpers import ADMIN_PASSWORD, query

LOGIN = "/api/v1/auth/login"


async def _login(client, password=ADMIN_PASSWORD, username="admin"):
    return await client.post(LOGIN, json={"username": username, "password": password})


async def test_login_sets_an_httponly_session_cookie(client, app_env):
    resp = await _login(client)
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"]
    assert get_settings().session_cookie_name in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert (await query(app_env, "SELECT count(*) AS n FROM sessions"))[0]["n"] == 1


async def test_wrong_password_and_unknown_user_are_401_and_audited(client, app_env):
    assert (await _login(client, password="wrong")).status_code == 401
    assert (await _login(client, username="ghost")).status_code == 401
    failed = await query(app_env, "SELECT detail FROM audit_log WHERE event_type = 'admin_login_failed' ORDER BY id")
    assert [f["detail"]["username"] for f in failed] == ["admin", "ghost"]


async def test_too_many_failures_lock_the_username(client, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILED_ATTEMPTS", "2")
    get_settings.cache_clear()
    assert (await _login(client, password="x")).status_code == 401
    assert (await _login(client, password="x")).status_code == 401
    locked = await _login(client)  # even the right password
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == str(get_settings().login_lockout_minutes * 60)


async def test_a_successful_login_resets_the_failure_count(client, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILED_ATTEMPTS", "2")
    get_settings.cache_clear()
    await _login(client, password="x")
    assert (await _login(client)).status_code == 200
    assert (await _login(client, password="x")).status_code == 401  # count restarted: 1 of 2


async def test_logout_revokes_the_session(admin):
    assert (await admin.get("/api/v1/auth/me")).status_code == 200
    assert (await admin.post("/api/v1/auth/logout")).status_code == 204
    assert (await admin.get("/api/v1/auth/me")).status_code == 401


async def test_logout_without_a_session_is_harmless(client):
    assert (await client.post("/api/v1/auth/logout")).status_code == 204


async def test_invalid_cookie_is_401(client):
    client.cookies.set(get_settings().session_cookie_name, "forged")
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_change_password_revokes_other_sessions_only(admin):
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as other:
        await _login(other)
        assert (await other.get("/api/v1/auth/me")).status_code == 200

        wrong = await admin.post("/api/v1/auth/change-password",
                                 json={"current_password": "nope", "new_password": "a-new-password"})
        assert wrong.status_code == 400  # not 401: the session itself is fine

        ok = await admin.post("/api/v1/auth/change-password",
                              json={"current_password": ADMIN_PASSWORD, "new_password": "a-new-password"})
        assert ok.status_code == 204
        assert (await admin.get("/api/v1/auth/me")).status_code == 200
        assert (await other.get("/api/v1/auth/me")).status_code == 401

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as fresh:
        assert (await _login(fresh)).status_code == 401
        assert (await _login(fresh, password="a-new-password")).status_code == 200


async def test_new_password_needs_eight_characters(admin):
    resp = await admin.post("/api/v1/auth/change-password",
                            json={"current_password": ADMIN_PASSWORD, "new_password": "short"})
    assert resp.status_code == 422
