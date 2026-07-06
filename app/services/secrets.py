"""
secrets.py
----------
Resolve referências a segredos (`auth_config['secret_ref']`) para o valor
real, sem que o resto do código precise de saber de onde vêm.

Em produção (GCP): o valor é lido do Secret Manager, com cache em memória
por processo (o processo Cloud Run é recriado com frequência, pelo que o
cache nunca fica desatualizado por muito tempo).

Em desenvolvimento local: cai para uma variável de ambiente com o mesmo
nome do secret_ref, para não obrigar a ter Secret Manager disponível
localmente.

Ver README.md secção "Segredos" para instruções de criação dos segredos
no Secret Manager e concessão de acesso à service account do Cloud Run.
"""
import logging
import os
from functools import lru_cache

from app.config import get_settings

logger = logging.getLogger(__name__)

_secret_manager_client = None


def _get_secret_manager_client():
    global _secret_manager_client
    if _secret_manager_client is None:
        from google.cloud import secretmanager  # import tardio - dependência opcional localmente
        _secret_manager_client = secretmanager.SecretManagerServiceClient()
    return _secret_manager_client


@lru_cache(maxsize=64)
def resolve_secret(secret_ref: str) -> str | None:
    """
    Resolve um secret_ref para o seu valor.

    - environment == 'local': lê de os.environ[secret_ref].
    - caso contrário: lê a versão 'latest' do segredo homónimo no Secret
      Manager do projeto GCP corrente (GOOGLE_CLOUD_PROJECT).
    """
    settings = get_settings()

    if settings.environment == "local":
        value = os.environ.get(secret_ref)
        if value is None:
            logger.warning("Segredo '%s' não encontrado nas variáveis de ambiente locais.", secret_ref)
        return value

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        logger.error("GOOGLE_CLOUD_PROJECT não definido - não é possível resolver segredos.")
        return None

    try:
        client = _get_secret_manager_client()
        name = f"projects/{project_id}/secrets/{secret_ref}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8")
    except Exception:
        logger.exception("Falha ao resolver segredo '%s' via Secret Manager.", secret_ref)
        return None
