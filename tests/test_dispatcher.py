"""
test_dispatcher.py
-------------------
Unit tests for dispatcher.py's single header-based auth mechanism (no
choice of auth types - see CLAUDE.md Decision 9) and its HTTP error
handling. Uses httpx.MockTransport so no real network call is ever made.

    pytest tests/test_dispatcher.py -v
"""
import json

import httpx
import pytest

from app.services.dispatcher import DeliveryError, deliver


def _patch_async_client(monkeypatch, handler):
    """Makes every httpx.AsyncClient() dispatcher.py constructs route through `handler` instead of the network."""

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.services.dispatcher.httpx.AsyncClient", _MockAsyncClient)


@pytest.mark.asyncio
async def test_default_header_is_x_api_key_with_no_prefix(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json={"ok": True})

    _patch_async_client(monkeypatch, handler)

    await deliver(
        base_url="https://destination.example/webhook",
        auth_config={"secret_ref": "whatever"},
        payload={"hello": "world"},
        resolved_secret="my-api-key-value",
    )

    assert captured["headers"]["x-api-key"] == "my-api-key-value"
    assert "authorization" not in captured["headers"]


@pytest.mark.asyncio
async def test_custom_header_with_value_prefix(monkeypatch):
    """Reproduces the old 'bearer' auth_type exactly, via header + value_prefix instead of a type."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json={"ok": True})

    _patch_async_client(monkeypatch, handler)

    await deliver(
        base_url="https://destination.example/webhook",
        auth_config={"header": "Authorization", "value_prefix": "Bearer ", "secret_ref": "whatever"},
        payload={"hello": "world"},
        resolved_secret="my-bearer-token",
    )

    assert captured["headers"]["authorization"] == "Bearer my-bearer-token"
    assert "x-api-key" not in captured["headers"]


@pytest.mark.asyncio
async def test_no_resolved_secret_sends_no_auth_header(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json={"ok": True})

    _patch_async_client(monkeypatch, handler)

    await deliver(
        base_url="https://destination.example/webhook",
        auth_config={"header": "Authorization", "value_prefix": "Bearer "},
        payload={"hello": "world"},
        resolved_secret=None,
    )

    assert "authorization" not in captured["headers"]
    assert "x-api-key" not in captured["headers"]
    assert captured["headers"]["content-type"] == "application/json"


@pytest.mark.asyncio
async def test_payload_and_url_are_sent_as_given(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    _patch_async_client(monkeypatch, handler)

    await deliver(
        base_url="https://destination.example/webhook",
        auth_config={},
        payload={"event": "ticket.created", "status": "new"},
        resolved_secret=None,
    )

    assert captured["url"] == "https://destination.example/webhook"
    assert captured["body"] == {"event": "ticket.created", "status": "new"}


@pytest.mark.asyncio
async def test_http_error_status_raises_delivery_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error at destination")

    _patch_async_client(monkeypatch, handler)

    with pytest.raises(DeliveryError, match="HTTP 500"):
        await deliver(
            base_url="https://destination.example/webhook",
            auth_config={},
            payload={},
            resolved_secret=None,
        )


@pytest.mark.asyncio
async def test_network_error_raises_delivery_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connection failure", request=request)

    _patch_async_client(monkeypatch, handler)

    with pytest.raises(DeliveryError, match="Network error"):
        await deliver(
            base_url="https://destination.example/webhook",
            auth_config={},
            payload={},
            resolved_secret=None,
        )
