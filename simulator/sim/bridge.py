"""
bridge.py
---------
The simulator's only outbound calls to Ticket Bridge: POST /api/v1/events
(authenticated with this system's inbound API key, exactly as a real
integrating system would) and GET /health for the Config page's
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


def post_event(settings: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    """Sends an IncomingEvent; returns the bridge's response ({conversation_id, created_outbox_ids})."""
    if not settings["api_key"]:
        raise BridgeError("No API key configured - set it on the Config page.")
    try:
        response = httpx.post(
            _url(settings, "/api/v1/events"),
            json=body,
            headers={"X-API-Key": settings["api_key"]},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.RequestError as exc:
        raise BridgeError(f"Could not reach the bridge at {settings['bridge_url']}: {exc}") from exc

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise BridgeError(f"Bridge returned HTTP {response.status_code}: {detail}", response.status_code)
    return response.json()


def check_health(settings: dict[str, str]) -> str:
    """Only proves the bridge is reachable - there's no side-effect-free way to validate an API key."""
    try:
        response = httpx.get(_url(settings, "/health"), timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise BridgeError(f"Health check against {settings['bridge_url']} failed: {exc}") from exc
    return response.text
