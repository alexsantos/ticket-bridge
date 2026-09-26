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

    # Optional. POST /api/v1/sync (manual trigger) accepts an admin session;
    # set this only for a non-human caller that can't log in - e.g. Cloud
    # Scheduler on Cloud Run, sent as X-Scheduler-Secret. Empty (the
    # default) disables that header entirely: there is no default value to
    # guess. The in-process scheduler above needs none of this - it calls
    # the same logic directly. See CLAUDE.md Decision 14.
    scheduler_shared_secret: str = ""

    # Admin frontend session cookie (see app/api/auth.py, app/security.py's
    # require_login). Distinct from SCHEDULER_SHARED_SECRET / api_keys -
    # this authenticates human admins of the Systems/Topics/Conversations/
    # Audit UI, not machine callers.
    session_cookie_name: str = "ticket_bridge_session"
    session_lifetime_hours: int = 12

    # Login brute-force throttling (app/api/auth.py): after this many failed
    # attempts for one username within the window, further attempts for it
    # get a 429 until the window slides past them. A successful login
    # resets the count.
    login_max_failed_attempts: int = 5
    login_lockout_minutes: int = 15

    # Key for encrypting outbound secrets entered in the frontend
    # (systems.outbound_secret_encrypted - see app/services/secret_store.py,
    # CLAUDE.md Decisions 13-14). Empty means outbound secrets can't be
    # stored, so no system can be given one.
    secrets_encryption_key: str = ""

    # Log level.
    log_level: str = "INFO"

    # Environment: 'local' | 'staging' | 'production' - used only to adjust
    # non-critical behavior (e.g. showing stack traces in the frontend).
    environment: str = "local"


@lru_cache
def get_settings() -> Settings:
    """Returns the cached configuration (read once per process)."""
    return Settings()
