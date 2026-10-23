"""
test_api_smoke.py
------------------
The app starts against a real, migrated database and serves both the API
and the static frontend.

    pytest tests/test_api_smoke.py -v
"""


async def test_health(client):
    resp = await client.get("/health")
    assert resp.json() == {"status": "ok"}


async def test_frontend_is_served_and_always_revalidated(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Ticket Bridge" in resp.text
    assert resp.headers["cache-control"] == "no-cache"


async def test_seeded_admin_can_log_in(admin):
    resp = await admin.get("/api/v1/auth/me")
    assert resp.json() == {"username": "admin"}
