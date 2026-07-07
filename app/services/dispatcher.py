"""
dispatcher.py
-------------
Responsible for physically delivering (via HTTP) an outbox entry to the
destination system, applying the header-based authentication configured
for that system (systems.auth_config).

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
    auth_config: dict[str, Any],
    payload: dict[str, Any],
    resolved_secret: str | None,
) -> None:
    """
    Sends the payload to the destination system.

    Authentication is a single generic mechanism, not a choice of types:
    if `resolved_secret` is set, it's placed into a header -
    `auth_config['header']` (default 'X-API-Key'), optionally prefixed
    with `auth_config['value_prefix']` (e.g. 'Bearer ', with a trailing
    space, for a standard bearer token; empty by default). This covers
    any "secret in a header" scheme - see CLAUDE.md Decision 9. Schemes
    that need something else (OAuth2 token exchange, HMAC request
    signing) aren't supported today.

    `resolved_secret` is the already-resolved value of the secret
    referenced in auth_config['secret_ref'] (see secrets.py) - the
    dispatcher never fetches secrets directly, it only applies them.
    """
    settings = get_settings()
    headers = {"Content-Type": "application/json"}

    if resolved_secret:
        header_name = auth_config.get("header", "X-API-Key")
        value_prefix = auth_config.get("value_prefix", "")
        headers[header_name] = f"{value_prefix}{resolved_secret}"

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
