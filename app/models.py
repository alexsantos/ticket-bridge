"""
models.py
---------
Domain models (Pydantic) that mirror the main database tables. Used
internally by the services; distinct from the API schemas (schemas.py),
which define the input/output contracts of the endpoints.

We keep this separation because the domain model can have internal fields
(e.g. the outbox's last_error) that don't make sense to expose as-is in the
public configuration API.
"""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas import CanonicalStatus


class System(BaseModel):
    code: str
    name: str
    base_url: str
    auth_config: dict[str, Any]
    active: bool
    topics: list[str]
    created_at: datetime
    updated_at: datetime


class Topic(BaseModel):
    code: str
    name: str
    description: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class ConversationParticipant(BaseModel):
    conversation_id: UUID
    system_code: str
    external_ref: str
    local_status: CanonicalStatus | None
    joined_at: datetime
    updated_at: datetime


class Conversation(BaseModel):
    conversation_id: UUID
    subject: str | None
    topic_code: str
    overall_status: CanonicalStatus
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    participants: list[ConversationParticipant] = []


class OutboxEntry(BaseModel):
    id: int
    conversation_id: UUID
    destination: str
    source: str
    payload: dict[str, Any]
    status: Literal["pending", "sent", "failed"]
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: datetime
    processed_at: datetime | None


class AuditLogEntry(BaseModel):
    id: int
    conversation_id: UUID | None
    system_code: str | None
    event_type: str
    detail: dict[str, Any]
    created_at: datetime
