"""
test_scheduler_and_lifespan.py
-------------------------------
The in-process sync scheduler (app/scheduler.py - Decision 2's update) and
the app's lifespan, which opens the database pool and starts/stops it.

    pytest tests/test_scheduler_and_lifespan.py -v
"""
import logging

import httpx

from app import scheduler
from app.config import get_settings


def _settings(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


async def test_scheduler_disabled_starts_nothing(monkeypatch):
    _settings(monkeypatch, SYNC_SCHEDULER_ENABLED="false")
    scheduler.start_scheduler()
    assert scheduler._scheduler is None
    scheduler.stop_scheduler()  # no-op
    get_settings.cache_clear()


async def test_scheduler_runs_the_sync_job_on_its_interval(monkeypatch):
    _settings(monkeypatch, SYNC_SCHEDULER_ENABLED="true", SYNC_INTERVAL_SECONDS="45")
    scheduler.start_scheduler()
    try:
        job = scheduler._scheduler.get_job("outbox_sync")
        assert job.trigger.interval.total_seconds() == 45
        assert job.max_instances == 1 and job.coalesce is True
    finally:
        scheduler.stop_scheduler()
        get_settings.cache_clear()
    assert scheduler._scheduler is None


async def test_scheduled_job_logs_results_and_survives_errors(monkeypatch, caplog):
    async def ok():
        return {"processed": 2, "success": 2, "failures": 0, "detail": []}

    async def boom():
        raise RuntimeError("database down")

    caplog.set_level(logging.INFO, logger="app.scheduler")
    monkeypatch.setattr(scheduler, "run_sync_batch", ok)
    await scheduler._run_sync_job()
    monkeypatch.setattr(scheduler, "run_sync_batch", boom)
    await scheduler._run_sync_job()  # must not raise: the scheduler keeps going

    assert "Scheduled sync run" in caplog.text
    assert "failed unexpectedly" in caplog.text


async def test_lifespan_opens_the_pool_and_serves_requests(database_url, monkeypatch):
    from app import database
    from app.main import app

    _settings(monkeypatch, DATABASE_URL=database_url, SYNC_SCHEDULER_ENABLED="false")
    try:
        async with app.router.lifespan_context(app):
            assert database._pool is not None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                topics = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "x"})
                assert topics.status_code == 401  # reached the database
    finally:
        database._pool = None
        get_settings.cache_clear()


async def test_connection_without_a_pool_is_a_clear_error():
    import pytest

    from app import database

    database._pool = None
    with pytest.raises(RuntimeError, match="not initialized"):
        async with database.get_connection():
            pass


def test_domain_models_accept_database_rows():
    from datetime import datetime, timezone

    from app.models import System

    now = datetime.now(timezone.utc)
    system = System(code="a", name="A", base_url="https://a", auth_config={}, verify_tls=True,
                    active=True, topics=["INFRA"], created_at=now, updated_at=now)
    assert system.topics == ["INFRA"]
