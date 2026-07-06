"""
database.py
-----------
Gestão do pool de ligações assíncronas ao PostgreSQL (via psycopg3 + pool).

Mantemos a interação com a base de dados em SQL explícito (sem ORM pesado)
propositadamente: o volume de tabelas é pequeno (6 tabelas), a lógica de
concorrência (FOR UPDATE SKIP LOCKED) precisa de controlo fino, e um ORM
acrescentaria complexidade sem benefício real neste contexto.
"""
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from app.config import get_settings

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None


async def init_pool() -> None:
    """Inicializa o pool de ligações. Chamado no arranque da aplicação (lifespan)."""
    global _pool
    settings = get_settings()
    _pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        kwargs={"row_factory": dict_row, "autocommit": False},
        open=False,
    )
    await _pool.open(wait=True, timeout=15)
    logger.info("Pool de ligações à base de dados inicializado.")


async def close_pool() -> None:
    """Fecha o pool de ligações. Chamado no encerramento da aplicação (lifespan)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        logger.info("Pool de ligações à base de dados encerrado.")


@asynccontextmanager
async def get_connection() -> AsyncIterator:
    """
    Context manager para obter uma ligação do pool dentro de um endpoint/serviço.

    Uso típico:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT ...")
    """
    if _pool is None:
        raise RuntimeError("Pool de ligações não inicializado - init_pool() não foi chamado.")
    async with _pool.connection() as conn:
        yield conn
