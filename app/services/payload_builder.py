"""
payload_builder.py
-------------------
Builds the fixed outbound payload sent to a destination system during
fan-out. There is exactly one shape, identical for every destination -
see `schemas.OutboundTicketEvent` and README.md's "Integration contract"
section. Extracted as a pure function (no FastAPI/DB dependency) so it
stays testable in isolation, matching this project's services/api split
(see sync_service.py for the same pattern).
"""
from typing import Any, Literal
from uuid import UUID

from app.schemas import CanonicalStatus, OutboundTicketEvent


def build_outbound_payload(
    *,
    is_known_participant: bool,
    conversation_id: UUID,
    status: CanonicalStatus,
    source_system: str,
    source_ref: str,
    external_ref: str | None,
    conversation_subject: str | None,
    metadata: dict[str, Any],
) -> OutboundTicketEvent:
    """
    `is_known_participant` mirrors `conversation_participants` already
    having a row for the destination (see
    correlation_service.list_fanout_destinations): if not, this is the
    destination's first time seeing this conversation and it gets
    'ticket.created' with no `external_ref` to reference; otherwise it
    gets 'ticket.updated' referencing its own already-known ticket.

    `metadata` is forwarded as-is from the triggering IncomingEvent - this
    is where structured business data (e.g. a confirmed insurance number)
    travels; the bridge itself never interprets it.
    """
    event: Literal["ticket.created", "ticket.updated"] = (
        "ticket.updated" if is_known_participant else "ticket.created"
    )
    return OutboundTicketEvent(
        event=event,
        conversation_id=conversation_id,
        status=status,
        source_system=source_system,
        source_ref=source_ref,
        external_ref=external_ref if is_known_participant else None,
        conversation_subject=conversation_subject,
        metadata=metadata,
    )
