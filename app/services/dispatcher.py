"""
dispatcher.py
-------------
Responsável por entregar fisicamente (via HTTP) uma entrada da outbox ao
sistema de destino, aplicando o tipo de autenticação configurado nesse
sistema (systems.auth_type / auth_config).

Isolado do resto da lógica de sincronização (api/sync.py) para que os
detalhes de "como falar HTTP com este sistema" não se espalhem pelo código
de orquestração.
"""
import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class DeliveryError(Exception):
    """Levantada quando a entrega a um sistema externo falha (HTTP erro ou timeout)."""


async def deliver(
    *,
    base_url: str,
    auth_type: str,
    auth_config: dict[str, Any],
    payload: dict[str, Any],
    resolved_secret: str | None,
) -> None:
    """
    Envia o payload ao sistema de destino.

    `resolved_secret` é o valor já resolvido do segredo referenciado em
    auth_config['secret_ref'] (ver secrets.py) - o dispatcher nunca vai
    buscar segredos diretamente, só os aplica.
    """
    settings = get_settings()
    headers = {"Content-Type": "application/json"}

    if auth_type == "api_key" and resolved_secret:
        header_name = auth_config.get("header", "X-API-Key")
        headers[header_name] = resolved_secret
    elif auth_type == "bearer" and resolved_secret:
        headers["Authorization"] = f"Bearer {resolved_secret}"
    elif auth_type == "basic" and resolved_secret:
        headers["Authorization"] = f"Basic {resolved_secret}"

    try:
        async with httpx.AsyncClient(timeout=settings.outbound_timeout_seconds) as client:
            response = await client.post(base_url, json=payload, headers=headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise DeliveryError(
            f"HTTP {exc.response.status_code} de {base_url}: {exc.response.text[:500]}"
        ) from exc
    except httpx.RequestError as exc:
        raise DeliveryError(f"Erro de rede ao contactar {base_url}: {exc}") from exc

    logger.info("Entrega bem-sucedida para %s", base_url)
