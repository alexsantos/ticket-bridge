"""
security.py
-----------
Autenticação das chamadas de entrada (inbound):

  1. POST /api/v1/events - autenticado com uma API key por sistema
     (tabela api_keys), enviada no header `X-API-Key`.
  2. POST /api/v1/sync - autenticado com um segredo partilhado simples
     (SCHEDULER_SHARED_SECRET), enviado pelo Cloud Scheduler no header
     `X-Scheduler-Secret`. Em produção, preferir OIDC nativo do Cloud
     Scheduler + Cloud Run (ver README.md, secção "Segurança do /sync").
  3. Endpoints de configuração/auditoria (/api/v1/systems, /api/v1/conversations,
     /api/v1/audit) - protegidos por IAM do Cloud Run (--no-allow-unauthenticated)
     ou por um proxy de autenticação à frente (ver README.md).

Nunca guardamos API keys em texto simples - apenas o hash SHA-256.
"""
import hashlib
import hmac

from fastapi import Header, HTTPException, status

from app.config import get_settings
from app.database import get_connection


def hash_key(raw_key: str) -> str:
    """Calcula o hash SHA-256 de uma chave em texto simples."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def authenticate_system(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """
    Dependency do FastAPI: valida a API key enviada por um sistema externo
    e devolve o código do sistema autenticado (para usar como `origem`
    nos eventos gravados).
    """
    key_hash = hash_key(x_api_key)
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT sistema FROM api_keys
                WHERE key_hash = %(hash)s AND active = TRUE
                """,
                {"hash": key_hash},
            )
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou revogada.",
        )
    return row["sistema"]


async def authenticate_scheduler(
    x_scheduler_secret: str = Header(..., alias="X-Scheduler-Secret")
) -> None:
    """Dependency do FastAPI: valida o segredo partilhado do Cloud Scheduler."""
    settings = get_settings()
    if not hmac.compare_digest(x_scheduler_secret, settings.scheduler_shared_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Segredo do scheduler inválido.",
        )
