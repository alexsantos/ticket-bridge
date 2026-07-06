"""
config.py
---------
Carrega e valida a configuração da aplicação a partir de variáveis de
ambiente. Em Cloud Run estas variáveis são definidas no serviço (ou
injetadas via Secret Manager para os valores sensíveis - ver README.md).

Não colocar segredos com valores por omissão aqui; os defaults servem
apenas para desenvolvimento local.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Ligação à base de dados. Em Cloud Run, ligar via Cloud SQL Auth Proxy
    # (socket unix) ou Cloud SQL Connector - ver README secção "Base de Dados".
    database_url: str = "postgresql://ticketbridge:ticketbridge@localhost:5432/ticketbridge"

    # Tamanho do pool de ligações (Cloud Run tem concorrência limitada por
    # instância; manter baixo evita esgotar ligações do Cloud SQL).
    db_pool_min_size: int = 1
    db_pool_max_size: int = 5

    # Nº de linhas da outbox processadas por cada invocação de /api/v1/sync.
    sync_batch_size: int = 20

    # Timeout (segundos) para chamadas HTTP de saída a cada sistema externo.
    outbound_timeout_seconds: float = 10.0

    # Chave partilhada que o Cloud Scheduler tem de enviar para poder
    # invocar /api/v1/sync (proteção simples contra invocação externa).
    scheduler_shared_secret: str = "change-me-in-production"

    # Nível de log.
    log_level: str = "INFO"

    # Ambiente: 'local' | 'staging' | 'production' - usado apenas para
    # ajustar comportamento não-crítico (ex: mostrar stacktraces no frontend).
    environment: str = "local"


@lru_cache
def get_settings() -> Settings:
    """Devolve a configuração em cache (lida uma única vez por processo)."""
    return Settings()
