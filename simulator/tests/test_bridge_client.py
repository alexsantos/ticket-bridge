"""
test_bridge_client.py
----------------------
The simulator's outbound side (sim/bridge.py) and the API error paths
around it: what the UI shows when the bridge is unreachable, rejects a
call, or isn't configured, plus the Settings page's connection test and
the frontend's cache headers.

    cd simulator && uv run pytest tests/test_bridge_client.py -v
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from sim import bridge
from sim.main import app

SETTINGS = {"bridge_url": "http://bridge/", "api_key": "k"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SIM_API_KEY", "sim-key")
    with TestClient(app) as c:
        yield c


def _respond(monkeypatch, method, response=None, error=None):
    def fake(url, **kwargs):
        if error:
            raise error
        return response
    monkeypatch.setattr(bridge.httpx, method, fake)


def test_post_event_needs_an_api_key():
    with pytest.raises(bridge.BridgeError, match="No API key"):
        bridge.post_event({**SETTINGS, "api_key": ""}, {})


def test_post_event_reports_an_unreachable_bridge(monkeypatch):
    _respond(monkeypatch, "post", error=httpx.ConnectError("refused"))
    with pytest.raises(bridge.BridgeError, match="Could not reach the bridge at http://bridge/"):
        bridge.post_event(SETTINGS, {})


def test_post_event_reports_a_non_json_error_body(monkeypatch):
    request = httpx.Request("POST", "http://bridge/api/v1/events")
    _respond(monkeypatch, "post", httpx.Response(502, text="Bad Gateway", request=request))
    with pytest.raises(bridge.BridgeError) as exc_info:
        bridge.post_event(SETTINGS, {})
    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Bridge returned HTTP 502: Bad Gateway"


def test_post_event_url_drops_a_trailing_slash(monkeypatch):
    seen = {}

    def fake(url, **kwargs):
        seen["url"] = url
        return httpx.Response(200, json={"conversation_id": "c", "created_outbox_ids": []},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(bridge.httpx, "post", fake)
    bridge.post_event(SETTINGS, {})
    assert seen["url"] == "http://bridge/api/v1/events"


def test_check_health(monkeypatch):
    request = httpx.Request("GET", "http://bridge/health")
    _respond(monkeypatch, "get", httpx.Response(200, text='{"status":"ok"}', request=request))
    assert bridge.check_health(SETTINGS) == '{"status":"ok"}'

    _respond(monkeypatch, "get", httpx.Response(503, request=request))
    with pytest.raises(bridge.BridgeError, match="Health check against http://bridge/ failed"):
        bridge.check_health(SETTINGS)


def test_config_test_endpoint(client, monkeypatch):
    request = httpx.Request("GET", "http://bridge/health")
    _respond(monkeypatch, "get", httpx.Response(200, text="ok", request=request))
    assert client.post("/api/config/test").json() == {"result": "ok"}

    _respond(monkeypatch, "get", error=httpx.ConnectError("refused"))
    resp = client.post("/api/config/test")
    assert resp.status_code == 502 and "Health check" in resp.json()["detail"]


def test_unknown_ticket_is_404(client):
    assert client.get("/api/tickets/999").status_code == 404
    assert client.post("/api/tickets/999/reply", json={"status": "resolved"}).status_code == 404


def test_reply_rejected_by_the_bridge_is_shown_and_not_recorded(client, monkeypatch):
    delivery = {
        "event": "ticket.created", "conversation_id": "c-1", "status": "new",
        "source_system": "system_a", "source_ref": "A-1",
    }
    client.post("/webhook", json=delivery)
    ticket_id = client.get("/api/tickets").json()[0]["id"]

    request = httpx.Request("POST", "http://bridge/api/v1/events")
    _respond(monkeypatch, "post", httpx.Response(403, json={"detail": "not subscribed"}, request=request))
    resp = client.post(f"/api/tickets/{ticket_id}/reply", json={"status": "resolved"})
    assert resp.status_code == 502 and "not subscribed" in resp.json()["detail"]

    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["status"] == "new" and len(detail["messages"]) == 1


def test_frontend_is_always_revalidated(client):
    for path in ("/", "/app.js", "/style.css"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["cache-control"] == "no-cache", path
