"""
main.py
-------
Ponto de entrada da aplicação FastAPI.

Responsabilidades:
  - Ciclo de vida (lifespan): abre/fecha o pool de ligações à base de dados.
  - Regista os routers de cada área funcional (app/api/*).
  - Serve o frontend estático de configuração/auditoria em "/".
  - Endpoint de health check em "/health" (usado pelo Cloud Run).

Correr localmente:
    uvicorn app.main:app --reload --port 8080

Correr em produção (Cloud Run):
    ver Dockerfile - usa o mesmo comando uvicorn, sem --reload.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import audit, conversations, events, sync, systems
from app.config import get_settings
from app.database import close_pool, init_pool

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    logger.info("Ticket Bridge iniciado (ambiente=%s).", settings.environment)
    yield
    await close_pool()
    logger.info("Ticket Bridge encerrado.")


app = FastAPI(
    title="Ticket Bridge",
    description="Serviço de correlação de tickets entre múltiplas aplicações de suporte.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(events.router)
app.include_router(sync.router)
app.include_router(systems.router)
app.include_router(conversations.router)
app.include_router(audit.router)


@app.get("/health", tags=["infra"])
async def health() -> dict:
    """Usado pelo Cloud Run / monitorização externa para verificar disponibilidade."""
    return {"status": "ok"}


# Frontend estático de configuração e auditoria (ver app/frontend/).
_frontend_dir = Path(__file__).parent / "frontend"
app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
