"""
schemas.py
----------
Request/response contracts for the REST endpoints. Kept separate from
models.py (internal domain) so that evolving the public API doesn't force
changes to the internal structure, and vice versa.
"""
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CanonicalStatus(StrEnum):
    """
    The bridge's one canonical ticket status vocabulary - every system
    speaks this directly, in both directions. There is no per-system
    mapping: a source system must translate its own internal status to
    one of these values before calling /api/v1/events, and a destination
    system must translate one of these values back to its own internal
    status upon receiving an outbound event. See README.md "Integration
    contract" and CLAUDE.md Decision 4.
    """
    NEW = "new"
    IN_PROGRESS = "in_progress"
    WAITING_THIRD_PARTY = "waiting_third_party"
    RESOLVED = "resolved"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Events received from an external system (POST /api/v1/events)
# ---------------------------------------------------------------------------
class IncomingEvent(BaseModel):
    """
    Payload that each external system sends to the bridge when a ticket is
    created or its status changes.

    If `conversation_id` is omitted, a new conversation is assumed to be
    created (e.g. system_a opened a new ticket for system_b). If provided,
    it's a status update to an existing conversation.
    """
    conversation_id: UUID | None = Field(
        default=None, description="Omit when creating a new conversation."
    )
    external_ref: str = Field(description="Ticket ID in the source system.")
    status: CanonicalStatus = Field(description="Status in the bridge's canonical vocabulary.")
    subject: str | None = Field(default=None, description="Free-text ticket title.")
    topic_code: str | None = Field(
        default=None,
        description="Ticket category/queue (e.g. 'INFRA'). Required when creating a "
                    "new conversation; the source system must be subscribed to it. "
                    "Immutable once the conversation exists.",
    )
    recipients: list[str] | None = Field(
        default=None,
        description="Codes of the systems to notify. If omitted, notifies "
                     "every system currently subscribed to the conversation's topic.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form structured data relevant to this specific update (e.g. "
                    "a confirmed insurance number, a resolution note) - forwarded as-is "
                    "to every fan-out destination in this event's OutboundTicketEvent. "
                    "Not accumulated across the conversation; each event's metadata "
                    "stands on its own, like a message in a chat.",
    )


class IncomingEventResponse(BaseModel):
    conversation_id: UUID
    created_outbox_ids: list[int]


# ---------------------------------------------------------------------------
# Events delivered to external systems (outbound, via POST /api/v1/sync)
# ---------------------------------------------------------------------------
class OutboundTicketEvent(BaseModel):
    """
    The fixed payload every destination system receives - identical shape
    and vocabulary for all of them, no per-system customization. This is
    the integration contract each system's own adapter code is written
    against; see README.md "Integration contract" and CLAUDE.md Decision 4.
    """
    event: Literal["ticket.created", "ticket.updated"]
    conversation_id: UUID
    status: CanonicalStatus
    source_system: str
    source_ref: str
    external_ref: str | None = Field(
        default=None,
        description="This destination's own known ticket ref. Absent on 'ticket.created' "
                    "(the destination has none yet); always present on 'ticket.updated'.",
    )
    conversation_subject: str | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured data the source system attached to this specific update "
                    "(see IncomingEvent.metadata) - e.g. a confirmed insurance number. "
                    "Forwarded as-is; the bridge does not interpret its contents.",
    )


# ---------------------------------------------------------------------------
# Synchronization (POST /api/v1/sync)
# ---------------------------------------------------------------------------
class SyncResult(BaseModel):
    processed: int
    success: int
    failures: int
    detail: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# System configuration (CRUD /api/v1/systems)
# ---------------------------------------------------------------------------
class SystemCreate(BaseModel):
    code: str
    name: str
    base_url: str
    auth_type: Literal["api_key", "bearer", "basic"] = "api_key"
    auth_config: dict[str, Any] = Field(default_factory=dict)
    active: bool = True
    topics: list[str] = Field(
        default_factory=list, description="Topic codes this system subscribes to."
    )


class SystemUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    auth_type: Literal["api_key", "bearer", "basic"] | None = None
    auth_config: dict[str, Any] | None = None
    active: bool | None = None
    topics: list[str] | None = None


class SystemOut(BaseModel):
    code: str
    name: str
    base_url: str
    auth_type: str
    active: bool
    topics: list[str]
    created_at: datetime
    updated_at: datetime
    # auth_config intentionally omitted from the default output - it may
    # contain references to secrets. See dedicated endpoint if needed.


# ---------------------------------------------------------------------------
# Topic configuration (CRUD /api/v1/topics)
# ---------------------------------------------------------------------------
class TopicCreate(BaseModel):
    code: str
    name: str
    description: str | None = None
    active: bool = True


class TopicUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    active: bool | None = None


class TopicOut(BaseModel):
    code: str
    name: str
    description: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Conversations / Audit (read-only queries)
# ---------------------------------------------------------------------------
class ParticipantOut(BaseModel):
    system_code: str
    external_ref: str
    local_status: CanonicalStatus | None
    updated_at: datetime


class ConversationOut(BaseModel):
    conversation_id: UUID
    subject: str | None
    topic_code: str
    overall_status: CanonicalStatus
    created_at: datetime
    updated_at: datetime
    participants: list[ParticipantOut]


class AuditLogOut(BaseModel):
    id: int
    conversation_id: UUID | None
    system_code: str | None
    event_type: str
    detail: dict[str, Any]
    created_at: datetime


class AuditLogPage(BaseModel):
    items: list[AuditLogOut]
    limit: int
    offset: int
    has_more: bool
