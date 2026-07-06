"""
schemas.py
----------
Request/response contracts for the REST endpoints. Kept separate from
models.py (internal domain) so that evolving the public API doesn't force
changes to the internal structure, and vice versa.
"""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


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
    status: str = Field(description="Status in the source system's vocabulary.")
    subject: str | None = None
    recipients: list[str] | None = Field(
        default=None,
        description="Codes of the systems to notify. If omitted, notifies "
                     "every other participant already associated with the conversation.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncomingEventResponse(BaseModel):
    conversation_id: UUID
    created_outbox_ids: list[int]


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
    status_mapping: dict[str, str] = Field(default_factory=dict)
    payload_template: dict[str, Any] = Field(default_factory=dict)
    active: bool = True


class SystemUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    auth_type: Literal["api_key", "bearer", "basic"] | None = None
    auth_config: dict[str, Any] | None = None
    status_mapping: dict[str, str] | None = None
    payload_template: dict[str, Any] | None = None
    active: bool | None = None


class SystemOut(BaseModel):
    code: str
    name: str
    base_url: str
    auth_type: str
    status_mapping: dict[str, str]
    active: bool
    created_at: datetime
    updated_at: datetime
    # auth_config intentionally omitted from the default output - it may
    # contain references to secrets. See dedicated endpoint if needed.


# ---------------------------------------------------------------------------
# Conversations / Audit (read-only queries)
# ---------------------------------------------------------------------------
class ParticipantOut(BaseModel):
    system_code: str
    external_ref: str
    local_status: str | None
    updated_at: datetime


class ConversationOut(BaseModel):
    conversation_id: UUID
    subject: str | None
    overall_status: str
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
