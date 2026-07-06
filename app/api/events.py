"""
events.py
---------
POST /api/v1/events

Ponto de entrada único para os sistemas externos comunicarem criação ou
mudança de estado de um ticket. Substitui a criação/atualização manual de
tickets no OSTicket.

Fluxo (tudo numa única transação):
  1. Autentica o sistema chamador via API key (dependency `authenticate_system`).
  2. Cria ou localiza a conversa (correlation_service).
  3. Compara o novo status com o último conhecido para este par
     (conversa, sistema de origem) - só prossegue se houver mudança real
     (proteção básica contra reenvios/eco).
  4. Faz fan-out: para cada outro participante da conversa (ou para a lista
     explícita de `destinatarios`), traduz o status para o vocabulário
     desse sistema e insere uma linha na outbox.
  5. Regista tudo em audit_log.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.database import get_connection
from app.schemas import IncomingEvent, IncomingEventResponse
from app.security import authenticate_system
from app.services import correlation_service, outbox_service, status_mapper
from app.services.audit_service import record_audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.post("", response_model=IncomingEventResponse)
async def receive_event(
    event: IncomingEvent,
    origem: str = Depends(authenticate_system),
) -> IncomingEventResponse:
    async with get_connection() as conn:
        async with conn.transaction():
            # 1. Correlação: cria ou localiza a conversa e atualiza o participante de origem.
            try:
                conversation_id, criada = await correlation_service.find_or_create_conversation(
                    conn,
                    conversation_id=event.conversation_id,
                    origem=origem,
                    ref_externa=event.ref_externa,
                    status=event.status,
                    assunto=event.assunto,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            await record_audit(
                conn,
                conversation_id=conversation_id,
                sistema=origem,
                evento_tipo="evento_recebido",
                detalhe=event.model_dump(mode="json"),
            )

            # 2. Determina os destinatários do fan-out.
            outros = await correlation_service.list_other_participants(
                conn, conversation_id=conversation_id, excluir=origem
            )
            destinos_permitidos = set(event.destinatarios) if event.destinatarios else None
            outbox_ids: list[int] = []

            for participante in outros:
                destino = participante["sistema"]
                if destinos_permitidos is not None and destino not in destinos_permitidos:
                    continue

                sistema_destino = await _get_system_config(conn, destino)
                if sistema_destino is None or not sistema_destino["active"]:
                    logger.warning("Destino '%s' inativo ou inexistente - a ignorar.", destino)
                    continue

                status_traduzido = status_mapper.interno_para_externo(
                    sistema_destino["status_mapping"], event.status
                )

                payload = _build_payload(
                    template=sistema_destino["payload_template"],
                    ref_externa=participante["ref_externa"],
                    status_mapeado=status_traduzido,
                    conversation_id=conversation_id,
                )

                outbox_id = await outbox_service.enqueue(
                    conn,
                    conversation_id=conversation_id,
                    destino=destino,
                    origem=origem,
                    payload=payload,
                )
                outbox_ids.append(outbox_id)

            await record_audit(
                conn,
                conversation_id=conversation_id,
                sistema=origem,
                evento_tipo="fanout_agendado",
                detalhe={"outbox_ids": outbox_ids, "destinos": [o["sistema"] for o in outros]},
            )

    return IncomingEventResponse(conversation_id=conversation_id, outbox_ids_criados=outbox_ids)


async def _get_system_config(conn, codigo: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT codigo, base_url, auth_type, auth_config, status_mapping, payload_template, active "
            "FROM systems WHERE codigo = %(codigo)s",
            {"codigo": codigo},
        )
        return await cur.fetchone()


def _build_payload(*, template: dict, ref_externa: str, status_mapeado: str, conversation_id: UUID) -> dict:
    """
    Aplica substituição simples de placeholders {ref_externa}, {status_mapeado}
    e {conversation_id} nos valores string do template configurado para o
    sistema de destino (systems.payload_template).
    """
    valores = {
        "ref_externa": ref_externa,
        "status_mapeado": status_mapeado,
        "conversation_id": str(conversation_id),
    }

    def _resolve(v):
        if isinstance(v, str):
            return v.format(**valores)
        if isinstance(v, dict):
            return {k: _resolve(val) for k, val in v.items()}
        return v

    if not template:
        # Sem template configurado - usa uma forma genérica razoável.
        return valores
    return _resolve(template)
