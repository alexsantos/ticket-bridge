"""
dispatcher.py
-------------
Responsible for physically delivering (via HTTP) an outbox entry to the
destination system, applying the authentication type configured for that
system (systems.auth_type / auth_config).

Isolated from the rest of the synchronization logic (api/sync.py) so that
the details of "how to speak HTTP to this system" don't spread across the
orchestration code.
"""
import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class DeliveryError(Exception):
    """Raised when delivery to an external system fails (HTTP error or timeout)."""


async def deliver(
    *,
    base_url: str,
    auth_type: str,
    auth_config: dict[str, Any],
    payload: dict[str, Any],
    resolved_secret: str | None,
) -> None:
    """
    Sends the payload to the destination system.

    `resolved_secret` is the already-resolved value of the secret
    referenced in auth_config['secret_ref'] (see secrets.py) - the
    dispatcher never fetches secrets directly, it only applies them.
    """
    settings = get_settings()
    headers = {"Content-Type": "application/json"}

    if auth_type == "api_key" and resolved_secret:
        header_name = auth_config.get("header", "X-API-Key")
        headers[header_name] = resolved_secret
    elif auth_type == "bearer" and resolved_secret:
        headers["Authorization"] = f"Bearer {resolved_secret}"
    elif auth_type == "basic" and resolved_secret:
        headers["Authorization"] = f"Basic {resolved_secret}"

    try:
        async with httpx.AsyncClient(timeout=settings.outbound_timeout_seconds) as client:
            response = await client.post(base_url, json=payload, headers=headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise DeliveryError(
            f"HTTP {exc.response.status_code} from {base_url}: {exc.response.text[:500]}"
        ) from exc
    except httpx.RequestError as exc:
        raise DeliveryError(f"Network error contacting {base_url}: {exc}") from exc

    logger.info("Delivery succeeded for %s", base_url)
