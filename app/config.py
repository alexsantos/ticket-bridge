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

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings' own `env_file` support (below) only populates this
# class's declared fields - it does not write into the real process
# os.environ. Anything that reads os.environ directly (e.g.
# app/services/secrets.py's local-mode secret_ref lookup, which needs to
# work for arbitrary per-system secret names that aren't declared Settings
# fields) would otherwise never see a value that only exists in .env.
# override=False (the default) means a real env var - a container's -e
# flag, a CI secret - always wins over .env.
load_dotenv()


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

    # Number of outbox rows processed per sync run (whether triggered by
    # the internal scheduler or manually via /api/v1/sync).
    sync_batch_size: int = 20

    # In-process scheduler (APScheduler) that triggers outbox sync
    # automatically, without depending on an external pinger like Google
    # Cloud Scheduler - see CLAUDE.md Decision 2. Suited to deployments
    # that run as a continuous process (a VM, a long-lived container)
    # rather than Cloud Run's scale-to-zero model. Disable if you'd rather
    # drive sync purely via the /api/v1/sync endpoint (e.g. still using
    # Cloud Scheduler, or a multi-instance deployment where you prefer a
    # single external trigger over N independent in-process schedulers).
    sync_scheduler_enabled: bool = True
    sync_interval_seconds: int = 120

    # Timeout (seconds) for outbound HTTP calls to each external system.
    outbound_timeout_seconds: float = 10.0

    # Shared secret required to call POST /api/v1/sync manually (simple
    # protection against unauthenticated external invocation). Unrelated
    # to the internal scheduler above, which calls the same logic directly
    # in-process and needs no authentication.
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
