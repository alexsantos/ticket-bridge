"""
conftest.py
-----------
Fixtures for the integration tests: the real app, over HTTP (httpx's
ASGITransport, no network), against a real Postgres - no mocked SQL, since
most of this project's logic *is* SQL (Decision 6: no ORM).

Where the Postgres comes from, in order:
  1. TEST_DATABASE_URL - a server where the tests may create databases
     (CI sets this to its Postgres service container).
  2. Otherwise a throwaway `postgres:17-alpine` container started with the
     docker CLI for the session, and removed afterwards.
  3. Otherwise the integration tests are skipped; the unit tests still run.

All migrations (seed data included: system_a / system_b, their dev keys,
the INFRA/SPM/SALES/PATIENT_ADMIN topics, the default admin) are applied
once to a template database, and every test gets a fresh copy of it
(CREATE DATABASE ... TEMPLATE), so tests never see each other's rows.

    uv run pytest                       # unit + integration, with coverage in CI
    TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest
"""
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest

from tests.helpers import ADMIN_PASSWORD, ADMIN_USERNAME, ENCRYPTION_KEY

MIGRATIONS = sorted((Path(__file__).parent.parent / "migrations").glob("*.sql"))
TEMPLATE_DB = "ticket_bridge_template"


def _with_database(url: str, dbname: str) -> str:
    return psycopg.conninfo.make_conninfo(url, dbname=dbname)


def _wait_for(url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            psycopg.connect(url, connect_timeout=2).close()
            return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@pytest.fixture(scope="session")
def postgres_server_url():
    """Conninfo of a Postgres server the tests may create databases on."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        _wait_for(url)
        yield url
        return

    if shutil.which("docker") is None:
        pytest.skip("No TEST_DATABASE_URL and no docker - skipping integration tests.")
    name = f"ticket-bridge-tests-{uuid.uuid4().hex[:8]}"
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=postgres",
         "-p", "127.0.0.1::5432", "postgres:17-alpine"],
        capture_output=True, text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"Couldn't start a Postgres container: {started.stderr.strip()}")
    try:
        port = subprocess.run(
            ["docker", "port", name, "5432/tcp"], capture_output=True, text=True, check=True
        ).stdout.strip().rsplit(":", 1)[1]
        url = f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres"
        _wait_for(url)
        # The entrypoint restarts the server once after initdb; wait for the final one.
        time.sleep(1)
        _wait_for(url)
        yield url
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True)


@pytest.fixture(scope="session")
def template_database(postgres_server_url):
    """A database with every migration applied, cloned for each test."""
    with psycopg.connect(postgres_server_url, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {TEMPLATE_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {TEMPLATE_DB}")
    with psycopg.connect(_with_database(postgres_server_url, TEMPLATE_DB), autocommit=True) as conn:
        for migration in MIGRATIONS:
            conn.execute(migration.read_text())
    return TEMPLATE_DB


@pytest.fixture
def database_url(postgres_server_url, template_database):
    """A fresh copy of the migrated template for this test."""
    name = f"ticket_bridge_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(postgres_server_url, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name} TEMPLATE {template_database}")
    yield _with_database(postgres_server_url, name)
    with psycopg.connect(postgres_server_url, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
async def app_env(database_url, monkeypatch):
    """Points the app's settings at this test's database and opens its pool."""
    from app import database
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("SYNC_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "local")  # session cookie without Secure, for http://test
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", ENCRYPTION_KEY)
    monkeypatch.setenv("SCHEDULER_SHARED_SECRET", "")
    get_settings.cache_clear()
    await database.init_pool()
    yield database_url
    await database.close_pool()
    database._pool = None
    get_settings.cache_clear()


@pytest.fixture
async def client(app_env):
    """An HTTP client for the app, not logged in."""
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin(client):
    """The same client, logged in as the seeded admin."""
    resp = await client.post("/api/v1/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture
def deliveries(monkeypatch):
    """
    Replaces the HTTP delivery step of a sync with a recorder, so syncs run
    their real SQL without any network call. Set `fail_for` to make
    deliveries to those base URLs fail.
    """
    from app.services import sync_service
    from app.services.dispatcher import DeliveryError

    class Recorder:
        def __init__(self):
            self.calls = []
            self.fail_for = set()

        async def __call__(self, *, base_url, auth_config, payload, resolved_secret, verify_tls=True):
            self.calls.append({
                "base_url": base_url, "auth_config": auth_config, "payload": payload,
                "secret": resolved_secret, "verify_tls": verify_tls,
            })
            if base_url in self.fail_for:
                raise DeliveryError(f"Network error contacting {base_url}: simulated")

    recorder = Recorder()
    monkeypatch.setattr(sync_service, "deliver", recorder)
    return recorder
