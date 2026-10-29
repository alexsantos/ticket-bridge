"""
test_api_archive_delete.py
---------------------------
Retiring systems against a real database (CLAUDE.md Decision 19):
archiving switches a system off and refuses its keys while keeping its
history; deleting is only allowed for a system that never exchanged a
message, and keeps its audit rows.

    pytest tests/test_api_archive_delete.py -v
"""
from tests.helpers import SYSTEM_A_KEY, SYSTEM_B_KEY, query, send_event


async def _new_system(admin, code="lab"):
    resp = await admin.post("/api/v1/systems", json={
        "code": code, "name": "Lab", "base_url": "https://lab.example/hook", "topics": ["INFRA"],
    })
    assert resp.status_code == 201, resp.text
    key = (await admin.post(f"/api/v1/systems/{code}/api-keys", json={})).json()["api_key"]
    return resp.json(), key


async def test_archive_switches_off_and_refuses_the_systems_keys(admin, app_env):
    archived = (await admin.post("/api/v1/systems/system_b/archive")).json()
    assert archived["archived_at"] and archived["active"] is False

    resp = await send_event(admin, SYSTEM_B_KEY, external_ref="B-1", status="new", topic_code="INFRA")
    assert resp.status_code == 403 and "archived" in resp.json()["detail"]

    # Still listed by the API (the panel hides it), history intact.
    assert "system_b" in [s["code"] for s in (await admin.get("/api/v1/systems")).json()]
    events = await query(app_env, "SELECT event_type FROM audit_log WHERE system_code = 'system_b' ORDER BY id")
    assert events[-1]["event_type"] == "system_archived"


async def test_an_archived_system_gets_no_messages_even_if_switched_back_on(admin, app_env):
    await admin.post("/api/v1/systems/system_b/archive")
    await admin.patch("/api/v1/systems/system_b", json={"active": True})
    await send_event(admin, SYSTEM_A_KEY, external_ref="A-1", status="new", topic_code="INFRA")
    assert await query(app_env, "SELECT id FROM outbox") == []


async def test_messages_queued_before_archiving_are_not_delivered(admin, deliveries):
    await send_event(admin, SYSTEM_A_KEY, external_ref="A-1", status="new", topic_code="INFRA")
    await admin.post("/api/v1/systems/system_b/archive")
    await admin.patch("/api/v1/systems/system_b", json={"active": True})
    result = (await admin.post("/api/v1/sync")).json()
    assert result["detail"][0]["result"] == "inactive_system_failure"
    assert deliveries.calls == []


async def test_unarchive_brings_it_back_inactive(admin):
    await admin.post("/api/v1/systems/system_b/archive")
    back = (await admin.post("/api/v1/systems/system_b/unarchive")).json()
    assert back["archived_at"] is None and back["active"] is False
    resp = await send_event(admin, SYSTEM_B_KEY, external_ref="B-1", status="new", topic_code="INFRA")
    assert resp.status_code == 200  # its key works again


async def test_archive_unknown_system(admin):
    assert (await admin.post("/api/v1/systems/nope/archive")).status_code == 404
    assert (await admin.post("/api/v1/systems/nope/unarchive")).status_code == 404


async def test_delete_a_system_that_never_exchanged_a_message(admin, app_env):
    system, key = await _new_system(admin)
    assert system["deletable"] is True

    assert (await admin.delete("/api/v1/systems/lab")).status_code == 204
    assert (await admin.get("/api/v1/systems/lab")).status_code == 404
    assert await query(app_env, "SELECT 1 FROM api_keys WHERE system_code = 'lab'") == []
    assert await query(app_env, "SELECT 1 FROM system_topic_subscriptions WHERE system_code = 'lab'") == []
    resp = await send_event(admin, key, external_ref="L-1", status="new", topic_code="INFRA")
    assert resp.status_code == 401

    # Its audit trail survives, code included.
    events = [r["event_type"] for r in await query(app_env, "SELECT event_type FROM audit_log WHERE system_code = 'lab' ORDER BY id")]
    assert events == ["system_config_created", "inbound_api_key_created", "system_deleted"]


async def test_a_system_with_messages_can_only_be_archived(admin):
    system, key = await _new_system(admin)
    await send_event(admin, key, external_ref="L-1", status="new", topic_code="INFRA")
    assert (await admin.get("/api/v1/systems/lab")).json()["deletable"] is False
    # Receiving counts too: system_b only ever received from it.
    assert (await admin.get("/api/v1/systems/system_b")).json()["deletable"] is False

    resp = await admin.delete("/api/v1/systems/lab")
    assert resp.status_code == 409 and "Archive it instead" in resp.json()["detail"]
    assert (await admin.delete("/api/v1/systems/system_b")).status_code == 409
    assert (await admin.delete("/api/v1/systems/nope")).status_code == 404


async def test_retiring_needs_a_session(client):
    assert (await client.post("/api/v1/systems/system_b/archive")).status_code == 401
    assert (await client.delete("/api/v1/systems/system_b")).status_code == 401
