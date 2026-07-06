"""
models.py
---------
Modelos de domínio (Pydantic) que espelham as tabelas principais da base de
dados. Usados internamente pelos serviços; distintos dos schemas de API
(schemas.py), que definem os contratos de entrada/saída dos endpoints.

Mantemos esta separação porque o modelo de domínio pode ter campos internos
(ex: last_error da outbox) que não fazem sentido expor tal e qual na API
pública de configuração.
"""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class System(BaseModel):
    codigo: str
    nome: str
    base_url: str
    auth_type: Literal["api_key", "bearer", "basic"]
    auth_config: dict[str, Any]
    status_mapping: dict[str, str]
    payload_template: dict[str, Any]
    active: bool
    created_at: datetime
    updated_at: datetime


class ConversationParticipant(BaseModel):
    conversation_id: UUID
    sistema: str
    ref_externa: str
    status_local: str | None
    joined_at: datetime
    updated_at: datetime


class Conversation(BaseModel):
    conversation_id: UUID
    assunto: str | None
    status_geral: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    participants: list[ConversationParticipant] = []


class OutboxEntry(BaseModel):
    id: int
    conversation_id: UUID
    destino: str
    origem: str
    payload: dict[str, Any]
    status: Literal["pending", "sent", "failed"]
    tentativas: int
    max_tentativas: int
    last_error: str | None
    created_at: datetime
    processed_at: datetime | None


class AuditLogEntry(BaseModel):
    id: int
    conversation_id: UUID | None
    sistema: str | None
    evento_tipo: str
    detalhe: dict[str, Any]
    created_at: datetime
