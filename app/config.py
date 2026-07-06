"""
config.py
---------
Loads and validates application configuration from environment variables.
On Cloud Run these variables are set on the service (or injected via Secret
Manager for sensitive values - see README.md).

Do not put secrets with default values here; the defaults are only meant
for local development.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database connection. On Cloud Run, connect via the Cloud SQL Auth
    # Proxy (unix socket) or the Cloud SQL Connector - see README's
    # "Database" section.
    database_url: str = "postgresql://ticketbridge:ticketbridge@localhost:5432/ticketbridge"

    # Connection pool size (Cloud Run has limited concurrency per instance;
    # keeping this low avoids exhausting Cloud SQL connections).
    db_pool_min_size: int = 1
    db_pool_max_size: int = 5

    # Number of outbox rows processed per /api/v1/sync invocation.
    sync_batch_size: int = 20

    # Timeout (seconds) for outbound HTTP calls to each external system.
    outbound_timeout_seconds: float = 10.0

    # Shared secret that Cloud Scheduler must send to invoke /api/v1/sync
    # (simple protection against external invocation).
    scheduler_shared_secret: str = "change-me-in-production"

    # Log level.
    log_level: str = "INFO"

    # Environment: 'local' | 'staging' | 'production' - used only to adjust
    # non-critical behavior (e.g. showing stack traces in the frontend).
    environment: str = "local"


@lru_cache
def get_settings() -> Settings:
    """Returns the cached configuration (read once per process)."""
    return Settings()
