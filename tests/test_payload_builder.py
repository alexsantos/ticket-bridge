"""
test_payload_builder.py
-------------------------
Pure unit tests (no database) for the fixed outbound payload shape and for
inbound status validation. Run with:

    pytest tests/test_payload_builder.py -v
"""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas import CanonicalStatus, IncomingEvent
from app.services.payload_builder import build_outbound_payload

CONVERSATION_ID = uuid4()


def test_ticket_created_has_no_external_ref():
    payload = build_outbound_payload(
        is_known_participant=False,
        conversation_id=CONVERSATION_ID,
        status=CanonicalStatus.NEW,
        source_system="system_a",
        source_ref="TICKET-1001",
        external_ref="whatever-was-passed-in",
        conversation_subject="Print queue stuck",
    )
    assert payload.event == "ticket.created"
    assert payload.external_ref is None


def test_ticket_updated_has_external_ref():
    payload = build_outbound_payload(
        is_known_participant=True,
        conversation_id=CONVERSATION_ID,
        status=CanonicalStatus.IN_PROGRESS,
        source_system="system_a",
        source_ref="TICKET-1001",
        external_ref="INC-2001",
        conversation_subject="Print queue stuck",
    )
    assert payload.event == "ticket.updated"
    assert payload.external_ref == "INC-2001"


def test_incoming_event_accepts_canonical_status():
    event = IncomingEvent(external_ref="TICKET-1001", status="new", topic_code="INFRA")
    assert event.status == CanonicalStatus.NEW


def test_incoming_event_rejects_non_canonical_status():
    with pytest.raises(ValidationError):
        IncomingEvent(external_ref="TICKET-1001", status="Open", topic_code="INFRA")
