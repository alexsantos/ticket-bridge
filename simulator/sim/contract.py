"""
contract.py
-----------
The simulator's own copy of the Ticket Bridge integration contract
(README.md "Integration contract" at the repo root). Deliberately not
imported from `app.schemas`: a real integrating system only has the
documented contract to build against, and the simulator should break the
same way theirs would if the contract changed underneath it.
"""
from typing import Any, Literal

from pydantic import BaseModel, Field

STATUSES = ("new", "in_progress", "waiting_third_party", "resolved", "closed")
Status = Literal["new", "in_progress", "waiting_third_party", "resolved", "closed"]


class InboundTicketEvent(BaseModel):
    """What the bridge POSTs to this system's webhook (the bridge's OutboundTicketEvent)."""

    event: Literal["ticket.created", "ticket.updated"]
    conversation_id: str
    # Readable reference (e.g. PATIENT_ADMIN-1042), for people only. Absent on
    # messages the bridge queued before it existed (0.17.0).
    conversation_ref: str | None = None
    status: Status
    source_system: str
    source_ref: str
    external_ref: str | None = None
    conversation_subject: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
