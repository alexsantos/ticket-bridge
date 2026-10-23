"""
test_sync_and_deliveries.py
----------------------------
The outbox end to end against a real database: POST /api/v1/sync delivers
pending entries (with the HTTP step replaced by the `deliveries` recorder
fixture), failures are retried until max_attempts and then marked failed,
and GET/POST /api/v1/deliveries list and retry them (Decision 16).

    pytest tests/test_sync_and_deliveries.py -v
"""
from app.config import get_settings
from tests.helpers import SYSTEM_A_KEY, query, send_event

SYSTEM_B_URL = "https://system-b.example.local/api/v2/patient-cases/hook"


async def _ticket(client, ref="A-1", **extra):
    resp = await send_event(client, SYSTEM_A_KEY, external_ref=ref, status="new", topic_code="INFRA", **extra)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _sync(client):
    resp = await client.post("/api/v1/sync")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_sync_delivers_pending_entries(admin, deliveries, app_env):
    created = await _ticket(admin, metadata={"note": "hi"})
    result = await _sync(admin)

    assert result["processed"] == 1 and result["success"] == 1
    call = deliveries.calls[0]
    assert call["base_url"] == SYSTEM_B_URL
    assert call["payload"]["conversation_id"] == created["conversation_id"]
    assert call["payload"]["metadata"] == {"note": "hi"}
    assert call["secret"] is None and call["verify_tls"] is True

    row = (await query(app_env, "SELECT status, processed_at FROM outbox"))[0]
    assert row["status"] == "sent" and row["processed_at"] is not None
    assert await query(app_env, "SELECT 1 FROM audit_log WHERE event_type = 'delivery_success'")
    assert (await _sync(admin))["processed"] == 0


async def test_stored_secret_and_tls_setting_reach_the_delivery(admin, deliveries):
    await admin.patch("/api/v1/systems/system_b", json={"outbound_secret": "b-secret", "verify_tls": False})
    await _ticket(admin)
    await _sync(admin)
    assert deliveries.calls[0]["secret"] == "b-secret"
    assert deliveries.calls[0]["verify_tls"] is False
    assert deliveries.calls[0]["auth_config"] == {"header": "Authorization", "value_prefix": "Bearer "}


async def test_failures_are_retried_then_marked_failed_then_retried_on_request(admin, deliveries, app_env):
    deliveries.fail_for.add(SYSTEM_B_URL)
    created = await _ticket(admin)
    outbox_id = created["created_outbox_ids"][0]

    for attempt in range(1, 6):
        result = await _sync(admin)
        assert result["failures"] == 1
    row = (await query(app_env, "SELECT status, attempts, last_error FROM outbox"))[0]
    assert row["status"] == "failed" and row["attempts"] == 5 and "simulated" in row["last_error"]
    assert (await _sync(admin))["processed"] == 0  # given up

    failed = (await admin.get("/api/v1/deliveries?status=failed")).json()["items"]
    assert [d["id"] for d in failed] == [outbox_id]
    assert failed[0]["event"] == "ticket.created" and failed[0]["conversation_subject"] is None

    deliveries.fail_for.clear()
    retry = (await admin.post("/api/v1/deliveries/retry", json={"outbox_ids": [outbox_id, 999]})).json()
    assert retry == {"retried": [outbox_id], "skipped": [999]}
    again = (await admin.post("/api/v1/deliveries/retry", json={"outbox_ids": [outbox_id]})).json()
    assert again == {"retried": [], "skipped": [outbox_id]}  # already pending

    assert (await _sync(admin))["success"] == 1
    row = (await query(app_env, "SELECT status, attempts, last_error FROM outbox"))[0]
    assert row == {"status": "sent", "attempts": 0, "last_error": None}
    audit = await query(app_env, "SELECT detail FROM audit_log WHERE event_type = 'delivery_retry_requested'")
    assert audit[0]["detail"]["previous_attempts"] == 5


async def test_destination_deactivated_after_queueing_fails_the_entry(admin, deliveries, app_env):
    await _ticket(admin)
    await admin.patch("/api/v1/systems/system_b", json={"active": False})
    result = await _sync(admin)
    assert result["detail"][0]["result"] == "inactive_system_failure"
    assert deliveries.calls == []


async def test_legacy_secret_ref_fails_instead_of_sending_without_auth(admin, deliveries, app_env):
    await _ticket(admin)
    await query(app_env, """UPDATE systems SET auth_config = auth_config || '{"secret_ref": "old"}' WHERE code = 'system_b'""")
    result = await _sync(admin)
    assert result["failures"] == 1 and "secret_ref" in result["detail"][0]["error"]
    assert deliveries.calls == []


async def test_deliveries_list_filters_and_pages(admin):
    first = await _ticket(admin, ref="A-1", subject="One")
    await _ticket(admin, ref="A-2", subject="Two")

    page = (await admin.get("/api/v1/deliveries?limit=1")).json()
    assert len(page["items"]) == 1 and page["has_more"] is True
    assert page["items"][0]["conversation_subject"] == "Two"  # newest first
    assert page["items"][0]["payload"]["source_ref"] == "A-2"

    by_conversation = (await admin.get(f"/api/v1/deliveries?conversation_id={first['conversation_id']}")).json()
    assert [d["conversation_subject"] for d in by_conversation["items"]] == ["One"]
    assert (await admin.get("/api/v1/deliveries?system_code=system_a")).json()["items"] == []
    assert len((await admin.get("/api/v1/deliveries?status=pending")).json()["items"]) == 2
    assert (await admin.get("/api/v1/deliveries?status=bogus")).status_code == 422


async def test_sync_accepts_the_scheduler_secret_when_configured(client, deliveries, monkeypatch):
    assert (await client.post("/api/v1/sync")).status_code == 401
    assert (await client.post("/api/v1/sync", headers={"X-Scheduler-Secret": "x"})).status_code == 401

    monkeypatch.setenv("SCHEDULER_SHARED_SECRET", "cron-secret")
    get_settings.cache_clear()
    resp = await client.post("/api/v1/sync", headers={"X-Scheduler-Secret": "cron-secret"})
    assert resp.status_code == 200
