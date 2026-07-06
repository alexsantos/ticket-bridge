"""
secrets.py
----------
Resolves secret references (`auth_config['secret_ref']`) to their real
value, without the rest of the code needing to know where they come from.

In production (GCP): the value is read from Secret Manager, with an
in-memory cache per process (the Cloud Run process is recreated often, so
the cache never stays stale for long).

In local development: falls back to an environment variable with the same
name as the secret_ref, so Secret Manager doesn't need to be available
locally.

See README.md, section "Secrets", for instructions on creating secrets in
Secret Manager and granting access to the Cloud Run service account.
"""
import logging
import os
from functools import lru_cache

from app.config import get_settings

logger = logging.getLogger(__name__)

_secret_manager_client = None


def _get_secret_manager_client():
    global _secret_manager_client
    if _secret_manager_client is None:
        from google.cloud import secretmanager  # lazy import - optional dependency locally
        _secret_manager_client = secretmanager.SecretManagerServiceClient()
    return _secret_manager_client


@lru_cache(maxsize=64)
def resolve_secret(secret_ref: str) -> str | None:
    """
    Resolves a secret_ref to its value.

    - environment == 'local': reads from os.environ[secret_ref].
    - otherwise: reads the 'latest' version of the matching secret in the
      current GCP project's Secret Manager (GOOGLE_CLOUD_PROJECT).
    """
    settings = get_settings()

    if settings.environment == "local":
        value = os.environ.get(secret_ref)
        if value is None:
            logger.warning("Secret '%s' not found in local environment variables.", secret_ref)
        return value

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        logger.error("GOOGLE_CLOUD_PROJECT not set - cannot resolve secrets.")
        return None

    try:
        client = _get_secret_manager_client()
        name = f"projects/{project_id}/secrets/{secret_ref}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8")
    except Exception:
        logger.exception("Failed to resolve secret '%s' via Secret Manager.", secret_ref)
        return None
