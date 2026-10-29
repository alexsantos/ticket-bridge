"""
test_api_inbox.py
------------------
Pull delivery against a real database (CLAUDE.md Decision 18): a system
with delivery_mode 'pull' is never called by the sync - it collects its
messages from GET /api/v1/inbox with its own API key, and acknowledges
them with POST /api/v1/inbox/ack.

Seed data: system_b subscribes to INFRA, so a ticket system_a opens on
INFRA is queued for it.

    pytest tests/test_api_inbox.py -v
"""
from tests.helpers import SYSTEM_A_KEY, SYSTEM_B_KEY, query, send_event

B_HEADERS = {"X-API-Key": SYSTEM_B_KEY}


async def _make_b_pull(admin):
    resp = await admin.patch("/api/v1/systems/system_b", json={"delivery_mode": "pull"})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _ticket(client, ref="A-1", **extra):
    resp = await send_event(client, SYSTEM_A_KEY, external_ref=ref, status="new", topic_code="INFRA", **extra)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_sync_leaves_a_pull_systems_messages_alone(admin, deliveries, app_env):
    await _make_b_pull(admin)
    await _ticket(admin)
    result = (await admin.post("/api/v1/sync")).json()
    assert result["processed"] == 0 and deliveries.calls == []
    row = (await query(app_env, "SELECT status, attempts FROM outbox"))[0]
    assert row == {"status": "pending", "attempts": 0}


async def test_collect_and_acknowledge(admin, app_env):
    await _make_b_pull(admin)
    first = await _ticket(admin, ref="A-1", subject="One", metadata={"note": "hi"})
    await _ticket(admin, ref="A-2", subject="Two")

    page = (await admin.get("/api/v1/inbox", headers=B_HEADERS)).json()
    assert page["has_more"] is False
    assert [m["message"]["conversation_subject"] for m in page["items"]] == ["One", "Two"]  # oldest first
    one = page["items"][0]
    assert one["delivery_id"] == first["created_outbox_ids"][0]
    assert one["message"]["event"] == "ticket.created"
    assert one["message"]["conversation_id"] == first["conversation_id"]
    assert one["message"]["metadata"] == {"note": "hi"}

    # Not acknowledged yet: still there on the next call (at-least-once).
    assert len((await admin.get("/api/v1/inbox", headers=B_HEADERS)).json()["items"]) == 2

    ack = (await admin.post("/api/v1/inbox/ack", headers=B_HEADERS,
                            json={"delivery_ids": [one["delivery_id"], one["delivery_id"], 999]})).json()
    assert ack == {"acknowledged": [one["delivery_id"]], "skipped": [999]}
    again = (await admin.post("/api/v1/inbox/ack", headers=B_HEADERS, json={"delivery_ids": [one["delivery_id"]]})).json()
    assert again == {"acknowledged": [], "skipped": [one["delivery_id"]]}

    remaining = (await admin.get("/api/v1/inbox", headers=B_HEADERS)).json()["items"]
    assert [m["message"]["conversation_subject"] for m in remaining] == ["Two"]

    row = (await query(app_env, "SELECT status, processed_at FROM outbox WHERE id = %(id)s", {"id": one["delivery_id"]}))[0]
    assert row["status"] == "sent" and row["processed_at"] is not None
    audit = await query(app_env, "SELECT system_code, detail FROM audit_log WHERE event_type = 'delivery_success'")
    assert audit == [{"system_code": "system_b", "detail": {"outbox_id": one["delivery_id"], "collected": True}}]


async def test_paging_with_limit(admin):
    await _make_b_pull(admin)
    for n in range(3):
        await _ticket(admin, ref=f"A-{n}")
    page = (await admin.get("/api/v1/inbox?limit=2", headers=B_HEADERS)).json()
    assert len(page["items"]) == 2 and page["has_more"] is True
    assert (await admin.get("/api/v1/inbox?limit=0", headers=B_HEADERS)).status_code == 422


async def test_polling_is_recorded_for_the_admin_panel(admin):
    assert (await _make_b_pull(admin))["last_polled_at"] is None
    await admin.get("/api/v1/inbox", headers=B_HEADERS)
    assert (await admin.get("/api/v1/systems/system_b")).json()["last_polled_at"] is not None


async def test_a_system_only_acknowledges_its_own_messages(admin, app_env):
    await _make_b_pull(admin)
    await admin.patch("/api/v1/systems/system_a", json={"delivery_mode": "pull"})
    for_b = await _ticket(admin)
    ack = (await admin.post("/api/v1/inbox/ack", headers={"X-API-Key": SYSTEM_A_KEY},
                            json={"delivery_ids": for_b["created_outbox_ids"]})).json()
    assert ack["acknowledged"] == []
    assert (await query(app_env, "SELECT status FROM outbox"))[0]["status"] == "pending"


async def test_push_systems_cant_use_the_inbox(client):
    resp = await client.get("/api/v1/inbox", headers=B_HEADERS)
    assert resp.status_code == 409 and "webhook" in resp.json()["detail"]
    assert (await client.post("/api/v1/inbox/ack", headers=B_HEADERS, json={"delivery_ids": [1]})).status_code == 409


async def test_inbox_needs_a_valid_api_key(client):
    assert (await client.get("/api/v1/inbox", headers={"X-API-Key": "nope"})).status_code == 401
    assert (await client.get("/api/v1/inbox")).status_code == 422


async def test_switching_back_to_push_delivers_what_was_waiting(admin, deliveries):
    await _make_b_pull(admin)
    await _ticket(admin)
    await admin.patch("/api/v1/systems/system_b", json={"delivery_mode": "push"})
    assert (await admin.post("/api/v1/sync")).json()["success"] == 1
    assert len(deliveries.calls) == 1


async def test_a_pull_system_needs_no_webhook_url(admin):
    created = await admin.post("/api/v1/systems", json={"code": "cloud", "name": "Cloud CRM", "delivery_mode": "pull"})
    assert created.status_code == 201, created.text
    assert created.json()["base_url"] is None and created.json()["delivery_mode"] == "pull"

    assert (await admin.post("/api/v1/systems", json={"code": "x", "name": "X"})).status_code == 422
    back_to_push = await admin.patch("/api/v1/systems/cloud", json={"delivery_mode": "push"})
    assert back_to_push.status_code == 400 and "base_url" in back_to_push.json()["detail"]
    ok = await admin.patch("/api/v1/systems/cloud", json={"delivery_mode": "push", "base_url": "https://crm.example/hook"})
    assert ok.json()["delivery_mode"] == "push"
