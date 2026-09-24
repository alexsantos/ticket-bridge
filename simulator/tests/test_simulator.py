"""
test_simulator.py
-----------------
Exercises the simulator through its HTTP API with the bridge faked out at
the httpx level (FakeBridge below records every POST /api/v1/events body
and answers like the real bridge would), plus the webhook exactly as the
bridge's dispatcher calls it.

    cd simulator && uv run pytest -v
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from sim import bridge
from sim.main import app

CONV = "11111111-2222-3333-4444-555555555555"


class FakeBridge:
    def __init__(self):
        self.events: list[dict] = []
        self.fail_with: int | None = None

    def post(self, url, json, headers, timeout):
        assert url.endswith("/api/v1/events")
        assert headers["X-API-Key"] == "sim-key"
        request = httpx.Request("POST", url)
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"detail": "nope"}, request=request)
        self.events.append(json)
        return httpx.Response(
            200, json={"conversation_id": json.get("conversation_id") or CONV, "created_outbox_ids": [1]},
            request=request,
        )


@pytest.fixture
def fake_bridge(monkeypatch):
    fake = FakeBridge()
    monkeypatch.setattr(bridge.httpx, "post", fake.post)
    return fake


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SIM_API_KEY", "sim-key")
    monkeypatch.setenv("SIM_SYSTEM_CODE", "sim_b")
    with TestClient(app) as c:
        yield c


def delivery(event="ticket.created", status="new", external_ref=None, metadata=None):
    return {
        "event": event, "conversation_id": CONV, "status": status,
        "source_system": "system_a", "source_ref": "CASE-1",
        "external_ref": external_ref, "conversation_subject": "Patient #1 - no insurance",
        "metadata": metadata or {},
    }


def test_received_ticket_is_matched_by_conversation_until_linked(client, fake_bridge):
    first = client.post("/webhook", json=delivery(metadata={"note": "please check"}))
    assert first.status_code == 200
    ref = first.json()["ref"]

    # Not linked yet, so the bridge sends ticket.created again for the same
    # conversation - it must land on the same local ticket, not a new one.
    again = client.post("/webhook", json=delivery(status="in_progress"))
    assert again.json()["ref"] == ref

    tickets = client.get("/api/tickets").json()
    assert len(tickets) == 1
    assert tickets[0]["origin"] == "received" and tickets[0]["linked"] is False
    detail = client.get(f"/api/tickets/{tickets[0]['id']}").json()
    assert [m["direction"] for m in detail["messages"]] == ["in", "in"]
    assert detail["messages"][0]["metadata"] == {"note": "please check"}
    assert detail["status"] == "in_progress"


def test_reply_sends_contract_body_and_links_ticket(client, fake_bridge):
    ref = client.post("/webhook", json=delivery()).json()["ref"]
    ticket_id = client.get("/api/tickets").json()[0]["id"]

    resp = client.post(f"/api/tickets/{ticket_id}/reply", json={
        "status": "resolved", "note": "Done", "metadata": {"insurance_number": "INS-1"},
    })
    assert resp.status_code == 200
    assert fake_bridge.events == [{
        "conversation_id": CONV, "external_ref": ref, "status": "resolved",
        "metadata": {"insurance_number": "INS-1", "note": "Done"},
    }]
    assert resp.json()["linked"] is True

    # Once linked, the bridge addresses us by our own ref.
    follow_up = client.post("/webhook", json=delivery(event="ticket.updated", status="closed", external_ref=ref))
    assert follow_up.json()["ref"] == ref
    assert client.get(f"/api/tickets/{ticket_id}").json()["status"] == "closed"


def test_new_ticket_is_sent_with_topic_and_subject(client, fake_bridge):
    resp = client.post("/api/tickets", json={
        "topic_code": "PATIENT_ADMIN", "subject": "Needs coverage check", "note": "hi",
    })
    assert resp.status_code == 201
    ticket = resp.json()
    assert fake_bridge.events == [{
        "external_ref": ticket["ref"], "status": "new", "subject": "Needs coverage check",
        "topic_code": "PATIENT_ADMIN", "metadata": {"note": "hi"},
    }]
    assert ticket["conversation_id"] == CONV and ticket["linked"] is True
    assert ticket["ref"].startswith("SIM-")


def test_rejected_new_ticket_leaves_no_local_ticket(client, fake_bridge):
    fake_bridge.fail_with = 403
    resp = client.post("/api/tickets", json={"topic_code": "SALES", "subject": "x"})
    assert resp.status_code == 502
    assert "HTTP 403" in resp.json()["detail"]
    assert client.get("/api/tickets").json() == []


def test_webhook_auth_requires_exact_configured_value(client):
    client.put("/api/config", json={
        "bridge_url": "http://bridge", "ref_prefix": "SIM",
        "inbound_header": "Authorization", "inbound_secret": "Bearer s3cret",
    })
    assert client.post("/webhook", json=delivery()).status_code == 401
    assert client.post("/webhook", json=delivery(), headers={"Authorization": "s3cret"}).status_code == 401
    assert client.post("/webhook", json=delivery(), headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_webhook_rejects_non_canonical_status(client):
    assert client.post("/webhook", json=delivery(status="Open")).status_code == 422


def test_config_never_returns_secrets(client):
    base = {"bridge_url": "http://bridge", "ref_prefix": "SIM"}
    saved = client.put("/api/config", json={**base, "api_key": "new-key", "inbound_secret": "s"}).json()
    assert "api_key" not in saved and "inbound_secret" not in saved
    assert saved["api_key_set"] and saved["inbound_secret_set"]

    # Blank secrets keep what's stored; the clear flag removes the webhook secret.
    kept = client.put("/api/config", json={**base, "api_key": ""}).json()
    assert kept["api_key_set"] and kept["inbound_secret_set"]
    cleared = client.put("/api/config", json={**base, "clear_inbound_secret": True}).json()
    assert cleared["inbound_secret_set"] is False and cleared["api_key_set"]
