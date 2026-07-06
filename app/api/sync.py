"""
sync.py
-------
POST /api/v1/sync

Chamado periodicamente pelo Cloud Scheduler (ex: a cada 1-2 minutos).
Processa um lote de entradas pendentes da outbox, entregando cada uma ao
sistema de destino via dispatcher.deliver().

Cada linha é processada na sua própria mini-transação (reserva com
FOR UPDATE SKIP LOCKED, tenta entregar, marca sent/failed) para que uma
falha numa entrega não bloqueie as restantes do lote.
"""
import logging

from fastapi import APIRouter, Depends

from app.config import get_settings
from app.database import get_connection
from app.schemas import SyncResult
from app.security import authenticate_scheduler
from app.services import outbox_service
from app.services.audit_service import record_audit
from app.services.dispatcher import DeliveryError, deliver
from app.services.secrets import resolve_secret

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


@router.post("", response_model=SyncResult, dependencies=[Depends(authenticate_scheduler)])
async def run_sync() -> SyncResult:
    settings = get_settings()
    sucesso = 0
    falhas = 0
    detalhe = []

    async with get_connection() as conn:
        async with conn.transaction():
            lote = await outbox_service.fetch_pending_batch(conn, limit=settings.sync_batch_size)

            for entrada in lote:
                sistema_destino = await _get_system_config(conn, entrada["destino"])

                if sistema_destino is None or not sistema_destino["active"]:
                    await outbox_service.mark_failed(
                        conn, outbox_id=entrada["id"], erro="Sistema de destino inativo ou inexistente."
                    )
                    falhas += 1
                    detalhe.append({"outbox_id": entrada["id"], "resultado": "falha_sistema_inativo"})
                    continue

                secret_ref = sistema_destino["auth_config"].get("secret_ref")
                resolved_secret = resolve_secret(secret_ref) if secret_ref else None

                try:
                    await deliver(
                        base_url=sistema_destino["base_url"],
                        auth_type=sistema_destino["auth_type"],
                        auth_config=sistema_destino["auth_config"],
                        payload=entrada["payload"],
                        resolved_secret=resolved_secret,
                    )
                except DeliveryError as exc:
                    await outbox_service.mark_failed(conn, outbox_id=entrada["id"], erro=str(exc))
                    await record_audit(
                        conn,
                        conversation_id=entrada["conversation_id"],
                        sistema=entrada["destino"],
                        evento_tipo="entrega_falha",
                        detalhe={"outbox_id": entrada["id"], "erro": str(exc)},
                    )
                    falhas += 1
                    detalhe.append({"outbox_id": entrada["id"], "resultado": "falha", "erro": str(exc)})
                else:
                    await outbox_service.mark_sent(conn, outbox_id=entrada["id"])
                    await record_audit(
                        conn,
                        conversation_id=entrada["conversation_id"],
                        sistema=entrada["destino"],
                        evento_tipo="entrega_sucesso",
                        detalhe={"outbox_id": entrada["id"]},
                    )
                    sucesso += 1
                    detalhe.append({"outbox_id": entrada["id"], "resultado": "sucesso"})

    processadas = sucesso + falhas
    logger.info("Sync concluído: %d processadas, %d sucesso, %d falhas.", processadas, sucesso, falhas)
    return SyncResult(processadas=processadas, sucesso=sucesso, falhas=falhas, detalhe=detalhe)


async def _get_system_config(conn, codigo: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT codigo, base_url, auth_type, auth_config, active FROM systems WHERE codigo = %(codigo)s",
            {"codigo": codigo},
        )
        return await cur.fetchone()
