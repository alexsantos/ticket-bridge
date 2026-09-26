"""
secrets.py
----------
Resolves secret references (`auth_config['secret_ref']`) to their real
value, without the rest of the code needing to know where they come from.

Lookup order, independent of ENVIRONMENT:
  1. An environment variable named `secret_ref.upper()` (e.g. .env on a VM
     or in docker compose). Uppercased because secret_ref values follow
     Secret Manager's own lowercase-with-underscores convention (e.g.
     `system_a_outbound_key`), while .env files conventionally use
     SCREAMING_SNAKE_CASE - env var names are case-sensitive, so this
     mapping has to be explicit rather than an exact-string match.
  2. Google Secret Manager, if GOOGLE_CLOUD_PROJECT is set (Cloud Run).

This used to depend on ENVIRONMENT: only 'local' read environment
variables, and everything else went to Secret Manager - which silently
broke `.env` secrets on a self-hosted VM with ENVIRONMENT=production (see
CLAUDE.md Decision 13).

Secrets set from the frontend don't go through here at all - they're
stored encrypted on the system row (app/services/secret_store.py).

Only successful lookups are cached, so a Secret Manager outage or a
secret created after startup doesn't stay "missing" for the life of the
process.
"""
import logging
import os

logger = logging.getLogger(__name__)

_secret_manager_client = None


def _get_secret_manager_client():
    global _secret_manager_client
    if _secret_manager_client is None:
        from google.cloud import secretmanager  # lazy import - optional dependency locally
        _secret_manager_client = secretmanager.SecretManagerServiceClient()
    return _secret_manager_client


_cache: dict[str, str] = {}


def resolve_secret(secret_ref: str) -> str | None:
    """Resolves a secret_ref to its value, or None if it can't be found anywhere."""
    if secret_ref in _cache:
        return _cache[secret_ref]

    value = os.environ.get(secret_ref.upper())
    if value is None:
        value = _from_secret_manager(secret_ref)
    if value is None:
        logger.warning(
            "Secret '%s' not found: no %s environment variable%s.",
            secret_ref, secret_ref.upper(),
            "" if os.environ.get("GOOGLE_CLOUD_PROJECT") else " (and GOOGLE_CLOUD_PROJECT is unset, so Secret Manager wasn't tried)",
        )
        return None
    _cache[secret_ref] = value
    return value


def _from_secret_manager(secret_ref: str) -> str | None:
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        return None
    try:
        client = _get_secret_manager_client()
        name = f"projects/{project_id}/secrets/{secret_ref}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8")
    except Exception:
        logger.exception("Failed to resolve secret '%s' via Secret Manager.", secret_ref)
        return None
