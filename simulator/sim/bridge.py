"""
bridge.py
---------
The simulator's only outbound calls to Ticket Bridge: POST /api/v1/events
and, in pull mode, GET /api/v1/inbox + POST /api/v1/inbox/ack (all
authenticated with this system's inbound API key, exactly as a real
integrating system would), and GET /health for the Settings page's
connection test. No admin endpoints - the simulator never needs an admin
session.
"""
from typing import Any

import httpx

TIMEOUT_SECONDS = 10.0


class BridgeError(Exception):
    """The bridge rejected the call or couldn't be reached. `detail` is safe to show in the UI."""

    def __init__(self, detail: str, status_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _url(settings: dict[str, str], path: str) -> str:
    return settings["bridge_url"].rstrip("/") + path


def _call(settings: dict[str, str], method: str, path: str, **kwargs: Any) -> httpx.Response:
    """An authenticated call to the bridge; raises BridgeError with a UI-safe reason."""
    if not settings["api_key"]:
        raise BridgeError("No API key configured - set it in Settings.")
    try:
        response = getattr(httpx, method)(
            _url(settings, path),
            headers={"X-API-Key": settings["api_key"]},
            timeout=TIMEOUT_SECONDS,
            **kwargs,
        )
    except httpx.RequestError as exc:
        raise BridgeError(f"Could not reach the bridge at {settings['bridge_url']}: {exc}") from exc

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise BridgeError(f"Bridge returned HTTP {response.status_code}: {detail}", response.status_code)
    return response


def post_event(settings: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    """Sends an IncomingEvent; returns the bridge's response ({conversation_id, created_outbox_ids})."""
    return _call(settings, "post", "/api/v1/events", json=body).json()


def fetch_inbox(settings: dict[str, str], limit: int = 50) -> dict[str, Any]:
    """Pull mode: this system's waiting messages, oldest first ({items, has_more})."""
    return _call(settings, "get", "/api/v1/inbox", params={"limit": limit}).json()


def ack_inbox(settings: dict[str, str], delivery_ids: list[int]) -> dict[str, Any]:
    """Pull mode: confirms these messages were processed, so the bridge stops offering them."""
    return _call(settings, "post", "/api/v1/inbox/ack", json={"delivery_ids": delivery_ids}).json()


def check_health(settings: dict[str, str]) -> str:
    """Only proves the bridge is reachable - there's no side-effect-free way to validate an API key."""
    try:
        response = httpx.get(_url(settings, "/health"), timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise BridgeError(f"Health check against {settings['bridge_url']} failed: {exc}") from exc
    return response.text
