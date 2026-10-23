"""
test_deliveries.py
-------------------
Unit tests for POST /api/v1/deliveries/retry's orchestration
(app/api/deliveries.py): which requested ids are reported as retried vs.
skipped, and that each retried entry gets an audit row carrying its
pre-reset attempts/error. The SQL itself (outbox_service.requeue_failed:
only 'failed' rows are touched) needs a real Postgres, which this project's
tests don't use - see tests/test_auth_security.py.

    pytest tests/test_deliveries.py -v
"""
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.api import deliveries
from app.schemas import RetryDeliveriesRequest


class _FakeConnection:
    @asynccontextmanager
    async def transaction(self):
        yield


@pytest.mark.asyncio
async def test_retry_reports_retried_and_skipped_and_audits_each_retry(monkeypatch):
    conversation_id = uuid4()
    requested_ids = []
    audits = []

    @asynccontextmanager
    async def fake_get_connection():
        yield _FakeConnection()

    async def fake_requeue_failed(conn, *, outbox_ids):
        requested_ids.extend(outbox_ids)
        # Only 3 is 'failed'; 1 is pending/sent, 99 doesn't exist.
        return [{
            "id": 3, "conversation_id": conversation_id, "destination": "system_b",
            "previous_attempts": 5, "previous_error": "CERTIFICATE_VERIFY_FAILED",
        }]

    async def fake_record_audit(conn, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(deliveries, "get_connection", fake_get_connection)
    monkeypatch.setattr(deliveries.outbox_service, "requeue_failed", fake_requeue_failed)
    monkeypatch.setattr(deliveries, "record_audit", fake_record_audit)

    result = await deliveries.retry_deliveries(RetryDeliveriesRequest(outbox_ids=[3, 1, 3, 99]))

    assert requested_ids == [3, 1, 99]  # duplicates dropped, order kept
    assert result.retried == [3]
    assert result.skipped == [1, 99]
    assert audits == [{
        "conversation_id": conversation_id,
        "system_code": "system_b",
        "event_type": "delivery_retry_requested",
        "detail": {"outbox_id": 3, "previous_attempts": 5, "previous_error": "CERTIFICATE_VERIFY_FAILED"},
    }]


def test_retry_request_needs_at_least_one_id():
    with pytest.raises(ValueError):
        RetryDeliveriesRequest(outbox_ids=[])
