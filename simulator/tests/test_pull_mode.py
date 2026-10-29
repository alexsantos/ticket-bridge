"""
test_pull_mode.py
------------------
The simulator collecting its messages from the bridge's inbox instead of
a webhook (pull mode - the bridge's CLAUDE.md Decision 18), with the
bridge faked at the httpx level: messages land on the same tickets a
webhook delivery would create, are acknowledged, and a redelivered
message (one acknowledged too late) is processed only once.

    cd simulator && uv run pytest tests/test_pull_mode.py -v
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from sim import bridge
from sim.main import app

CONV = "11111111-2222-3333-4444-555555555555"


def message(event="ticket.created", status="new", external_ref=None, note="please check"):
    return {
        "event": event, "conversation_id": CONV, "status": status,
        "source_system": "system_a", "source_ref": "CASE-1", "external_ref": external_ref,
        "conversation_subject": "Patient #1 - no insurance", "metadata": {"note": note},
    }


class FakeInbox:
    """The bridge's GET /api/v1/inbox and POST /api/v1/inbox/ack."""

    def __init__(self):
        self.waiting: list[dict] = []
        self.acked: list[int] = []
        self.fail_with: int | None = None

    def add(self, delivery_id, msg):
        self.waiting.append({"delivery_id": delivery_id, "queued_at": "2026-10-29T10:00:00Z", "message": msg})

    def get(self, url, headers, timeout, params):
        assert url == "http://bridge/api/v1/inbox" and headers["X-API-Key"] == "sim-key"
        request = httpx.Request("GET", url)
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"detail": "nope"}, request=request)
        items = self.waiting[: params["limit"]]
        return httpx.Response(200, json={"items": items, "has_more": len(self.waiting) > len(items)}, request=request)

    def post(self, url, headers, timeout, json):
        assert url == "http://bridge/api/v1/inbox/ack"
        self.acked.extend(json["delivery_ids"])
        self.waiting = [i for i in self.waiting if i["delivery_id"] not in json["delivery_ids"]]
        return httpx.Response(200, json={"acknowledged": json["delivery_ids"], "skipped": []},
                              request=httpx.Request("POST", url))


@pytest.fixture
def inbox(monkeypatch):
    fake = FakeInbox()
    monkeypatch.setattr(bridge.httpx, "get", fake.get)
    monkeypatch.setattr(bridge.httpx, "post", fake.post)
    return fake


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SIM_API_KEY", "sim-key")
    monkeypatch.setenv("SIM_BRIDGE_URL", "http://bridge")
    monkeypatch.setenv("SIM_DELIVERY_MODE", "pull")
    with TestClient(app) as c:
        yield c


def test_collects_acknowledges_and_files_messages_on_tickets(client, inbox):
    inbox.add(1, message())
    inbox.add(2, message(status="in_progress", note="still checking"))

    assert client.post("/api/inbox/collect").json() == {"collected": 2}
    assert inbox.acked == [1, 2] and inbox.waiting == []

    tickets = client.get("/api/tickets").json()
    assert len(tickets) == 1 and tickets[0]["origin"] == "received"
    detail = client.get(f"/api/tickets/{tickets[0]['id']}").json()
    assert [m["metadata"]["note"] for m in detail["messages"]] == ["please check", "still checking"]
    assert detail["status"] == "in_progress"

    status = client.get("/api/config").json()["inbox"]
    assert status["last_count"] == 2 and status["last_error"] is None and status["last_collected_at"]


def test_a_redelivered_message_is_processed_once(client, inbox):
    inbox.add(1, message())
    client.post("/api/inbox/collect")
    inbox.add(1, message())  # the bridge offers it again, e.g. our ack was lost
    assert client.post("/api/inbox/collect").json() == {"collected": 0}
    assert inbox.acked == [1, 1]  # acknowledged again, so the bridge stops offering it
    ticket_id = client.get("/api/tickets").json()[0]["id"]
    assert len(client.get(f"/api/tickets/{ticket_id}").json()["messages"]) == 1


def test_collects_every_page(client, inbox, monkeypatch):
    for n in range(1, 4):
        inbox.add(n, message(status="new" if n == 1 else "in_progress", note=f"#{n}"))
    real_fetch = bridge.fetch_inbox
    monkeypatch.setattr(bridge, "fetch_inbox", lambda settings, limit=50: real_fetch(settings, limit=2))
    assert client.post("/api/inbox/collect").json() == {"collected": 3}
    assert inbox.acked == [1, 2, 3]


def test_bridge_errors_are_shown(client, inbox):
    inbox.fail_with = 409
    resp = client.post("/api/inbox/collect")
    assert resp.status_code == 502 and "HTTP 409" in resp.json()["detail"]
    assert "HTTP 409" in client.get("/api/config").json()["inbox"]["last_error"]


def test_collect_needs_pull_mode(client, inbox):
    client.put("/api/config", json={"bridge_url": "http://bridge", "ref_prefix": "SIM", "delivery_mode": "push"})
    assert client.post("/api/inbox/collect").status_code == 409
    assert client.get("/api/config").json()["delivery_mode"] == "push"


def test_background_loop_collects_without_being_asked(tmp_path, monkeypatch, inbox):
    import time

    from sim import main

    monkeypatch.setenv("SIM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SIM_API_KEY", "sim-key")
    monkeypatch.setenv("SIM_BRIDGE_URL", "http://bridge")
    monkeypatch.setenv("SIM_DELIVERY_MODE", "pull")
    monkeypatch.setattr(main, "POLL_SECONDS", 0.05)
    inbox.add(1, message())
    with TestClient(app) as c:
        deadline = time.monotonic() + 5
        while not c.get("/api/tickets").json() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(c.get("/api/tickets").json()) == 1
        assert inbox.acked == [1]

        # A bridge error is logged and the loop keeps going.
        inbox.fail_with = 503
        time.sleep(0.2)
        assert "HTTP 503" in c.get("/api/config").json()["inbox"]["last_error"]
