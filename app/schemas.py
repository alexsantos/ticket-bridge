"""
schemas.py
----------
Contratos de entrada e saída (request/response) dos endpoints REST.
Separados de models.py (domínio interno) para que a evolução da API pública
não obrigue a alterar a estrutura interna, e vice-versa.
"""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Eventos recebidos de um sistema externo (POST /api/v1/events)
# ---------------------------------------------------------------------------
class IncomingEvent(BaseModel):
    """
    Payload que cada sistema externo envia ao bridge quando um ticket é
    criado ou o seu estado muda.

    Se `conversation_id` vier vazio, assume-se criação de uma nova conversa
    (ex: sistema_a abriu um ticket novo para sistema_b). Se vier preenchido,
    é uma atualização de estado de uma conversa já existente.
    """
    conversation_id: UUID | None = Field(
        default=None, description="Omitir ao criar uma nova conversa."
    )
    ref_externa: str = Field(description="ID do ticket no sistema de origem.")
    status: str = Field(description="Estado no vocabulário do sistema de origem.")
    assunto: str | None = None
    destinatarios: list[str] | None = Field(
        default=None,
        description="Códigos dos sistemas a notificar. Se omitido, notifica "
                     "todos os outros participantes já associados à conversa.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncomingEventResponse(BaseModel):
    conversation_id: UUID
    outbox_ids_criados: list[int]


# ---------------------------------------------------------------------------
# Sincronização (POST /api/v1/sync)
# ---------------------------------------------------------------------------
class SyncResult(BaseModel):
    processadas: int
    sucesso: int
    falhas: int
    detalhe: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Configuração de sistemas (CRUD /api/v1/systems)
# ---------------------------------------------------------------------------
class SystemCreate(BaseModel):
    codigo: str
    nome: str
    base_url: str
    auth_type: Literal["api_key", "bearer", "basic"] = "api_key"
    auth_config: dict[str, Any] = Field(default_factory=dict)
    status_mapping: dict[str, str] = Field(default_factory=dict)
    payload_template: dict[str, Any] = Field(default_factory=dict)
    active: bool = True


class SystemUpdate(BaseModel):
    nome: str | None = None
    base_url: str | None = None
    auth_type: Literal["api_key", "bearer", "basic"] | None = None
    auth_config: dict[str, Any] | None = None
    status_mapping: dict[str, str] | None = None
    payload_template: dict[str, Any] | None = None
    active: bool | None = None


class SystemOut(BaseModel):
    codigo: str
    nome: str
    base_url: str
    auth_type: str
    status_mapping: dict[str, str]
    active: bool
    created_at: datetime
    updated_at: datetime
    # auth_config propositadamente omitido do output por defeito - pode
    # conter referências a segredos. Ver endpoint dedicado se for necessário.


# ---------------------------------------------------------------------------
# Conversas / Auditoria (consulta, read-only)
# ---------------------------------------------------------------------------
class ParticipantOut(BaseModel):
    sistema: str
    ref_externa: str
    status_local: str | None
    updated_at: datetime


class ConversationOut(BaseModel):
    conversation_id: UUID
    assunto: str | None
    status_geral: str
    created_at: datetime
    updated_at: datetime
    participants: list[ParticipantOut]


class AuditLogOut(BaseModel):
    id: int
    conversation_id: UUID | None
    sistema: str | None
    evento_tipo: str
    detalhe: dict[str, Any]
    created_at: datetime
