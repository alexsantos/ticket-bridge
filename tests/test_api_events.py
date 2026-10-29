"""
test_api_events.py
-------------------
POST /api/v1/events against a real database: correlation (create vs.
update a conversation), topic rules for new conversations (CLAUDE.md
Decision 8), and fan-out into the outbox - which systems get a row, and
the payload shape each one gets (Decision 4).

Seed data used: system_a subscribes to INFRA and PATIENT_ADMIN; system_b to
INFRA, SPM and PATIENT_ADMIN; nobody to SALES.

    pytest tests/test_api_events.py -v
"""
from tests.helpers import SYSTEM_A_KEY, SYSTEM_B_KEY, query, send_event


async def _new_ticket(client, **overrides):
    event = {"external_ref": "A-1", "status": "new", "topic_code": "INFRA", "subject": "Printer offline"}
    resp = await send_event(client, SYSTEM_A_KEY, **{**event, **overrides})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_new_conversation_fans_out_to_subscribed_systems(client, app_env):
    created = await _new_ticket(client, metadata={"note": "Ward 3"})

    rows = await query(app_env, "SELECT destination, source, status, payload FROM outbox")
    assert [(r["destination"], r["source"], r["status"]) for r in rows] == [("system_b", "system_a", "pending")]
    payload = rows[0]["payload"]
    assert payload["event"] == "ticket.created"
    assert payload["external_ref"] is None
    assert payload["source_ref"] == "A-1"
    assert payload["conversation_subject"] == "Printer offline"
    assert payload["metadata"] == {"note": "Ward 3"}
    assert payload["conversation_id"] == created["conversation_id"]
    assert len(created["created_outbox_ids"]) == 1

    audit = await query(app_env, "SELECT event_type FROM audit_log ORDER BY id")
    assert [a["event_type"] for a in audit] == ["conversation_created", "event_received", "fanout_scheduled"]


async def test_reply_updates_the_known_participant(client, app_env):
    created = await _new_ticket(client)
    reply = await send_event(
        client, SYSTEM_B_KEY,
        conversation_id=created["conversation_id"], external_ref="B-9", status="in_progress",
    )
    assert reply.status_code == 200

    rows = await query(app_env, "SELECT payload FROM outbox WHERE destination = 'system_a'")
    assert rows[0]["payload"]["event"] == "ticket.updated"
    assert rows[0]["payload"]["external_ref"] == "A-1"
    assert rows[0]["payload"]["status"] == "in_progress"

    participants = await query(
        app_env, "SELECT system_code, external_ref, local_status FROM conversation_participants ORDER BY system_code"
    )
    assert participants == [
        {"system_code": "system_a", "external_ref": "A-1", "local_status": "new"},
        {"system_code": "system_b", "external_ref": "B-9", "local_status": "in_progress"},
    ]


async def test_topic_on_an_update_must_match(client):
    created = await _new_ticket(client)
    resp = await send_event(
        client, SYSTEM_B_KEY,
        conversation_id=created["conversation_id"], external_ref="B-9", status="new", topic_code="SPM",
    )
    assert resp.status_code == 409


async def test_unknown_conversation_is_404(client):
    resp = await send_event(
        client, SYSTEM_A_KEY,
        conversation_id="00000000-0000-0000-0000-000000000000", external_ref="A-1", status="new",
    )
    assert resp.status_code == 404


async def test_new_conversation_needs_a_topic(client):
    resp = await send_event(client, SYSTEM_A_KEY, external_ref="A-1", status="new")
    assert resp.status_code == 422


async def test_unknown_topic_is_404(client):
    resp = await send_event(client, SYSTEM_A_KEY, external_ref="A-1", status="new", topic_code="NOPE")
    assert resp.status_code == 404


async def test_inactive_topic_is_rejected(admin):
    await admin.patch("/api/v1/topics/INFRA", json={"active": False})
    resp = await send_event(admin, SYSTEM_A_KEY, external_ref="A-1", status="new", topic_code="INFRA")
    assert resp.status_code == 409


async def test_source_must_subscribe_to_the_topic(client):
    resp = await send_event(client, SYSTEM_A_KEY, external_ref="A-1", status="new", topic_code="SPM")
    assert resp.status_code == 403


async def test_recipients_narrow_the_fan_out(client, app_env):
    await _new_ticket(client, recipients=["someone_else"])
    assert await query(app_env, "SELECT id FROM outbox") == []


async def test_inactive_destination_is_skipped(admin, app_env):
    await admin.patch("/api/v1/systems/system_b", json={"active": False})
    await _new_ticket(admin)
    assert await query(app_env, "SELECT id FROM outbox") == []


async def test_invalid_or_missing_api_key(client):
    bad = await send_event(client, "not-a-key", external_ref="A-1", status="new", topic_code="INFRA")
    assert bad.status_code == 401
    missing = await client.post("/api/v1/events", json={"external_ref": "A-1", "status": "new"})
    assert missing.status_code == 422


async def test_status_outside_the_canonical_vocabulary_is_rejected(client):
    resp = await send_event(client, SYSTEM_A_KEY, external_ref="A-1", status="Open", topic_code="INFRA")
    assert resp.status_code == 422


async def test_overall_status_follows_the_latest_event(admin, app_env):
    created = await _new_ticket(admin)
    cid = created["conversation_id"]
    before = (await admin.get(f"/api/v1/conversations/{cid}")).json()
    assert before["overall_status"] == "new"

    await send_event(admin, SYSTEM_B_KEY, conversation_id=cid, external_ref="B-9", status="resolved")
    after = (await admin.get(f"/api/v1/conversations/{cid}")).json()
    assert after["overall_status"] == "resolved"
    assert after["updated_at"] > before["updated_at"]  # lists sort by latest activity

    # Whichever system sent it - the latest event wins.
    await send_event(admin, SYSTEM_A_KEY, conversation_id=cid, external_ref="A-1", status="closed")
    assert (await admin.get(f"/api/v1/conversations/{cid}")).json()["overall_status"] == "closed"


async def test_rejected_update_leaves_the_status_alone(admin):
    created = await _new_ticket(admin)
    cid = created["conversation_id"]
    resp = await send_event(admin, SYSTEM_B_KEY, conversation_id=cid, external_ref="B-9",
                            status="resolved", topic_code="SPM")
    assert resp.status_code == 409
    assert (await admin.get(f"/api/v1/conversations/{cid}")).json()["overall_status"] == "new"
