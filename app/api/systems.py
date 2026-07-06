"""
systems.py
----------
CRUD de configuração dos sistemas federados (/api/v1/systems).

Usado pelo frontend de configuração para que adicionar/alterar/desativar
um sistema (o "terceiro sistema" do desenho original) seja uma operação
de configuração em runtime, sem deploy de código novo.

Nota de segurança: estes endpoints devem estar atrás de autenticação
IAM do Cloud Run ou de um proxy de autenticação - não têm autenticação
própria porque se assume acesso apenas por administradores (ver README.md).
"""
from fastapi import APIRouter, HTTPException

from app.database import get_connection
from app.schemas import SystemCreate, SystemOut, SystemUpdate
from app.services.audit_service import record_audit

router = APIRouter(prefix="/api/v1/systems", tags=["systems"])


@router.get("", response_model=list[SystemOut])
async def list_systems() -> list[SystemOut]:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT codigo, nome, base_url, auth_type, status_mapping, active, created_at, updated_at "
                "FROM systems ORDER BY codigo"
            )
            rows = await cur.fetchall()
    return [SystemOut(**row) for row in rows]


@router.get("/{codigo}", response_model=SystemOut)
async def get_system(codigo: str) -> SystemOut:
    row = await _fetch_one(codigo)
    if row is None:
        raise HTTPException(status_code=404, detail="Sistema não encontrado.")
    return SystemOut(**row)


@router.post("", response_model=SystemOut, status_code=201)
async def create_system(payload: SystemCreate) -> SystemOut:
    async with get_connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        """
                        INSERT INTO systems
                            (codigo, nome, base_url, auth_type, auth_config, status_mapping, payload_template, active)
                        VALUES
                            (%(codigo)s, %(nome)s, %(base_url)s, %(auth_type)s, %(auth_config)s,
                             %(status_mapping)s, %(payload_template)s, %(active)s)
                        RETURNING codigo, nome, base_url, auth_type, status_mapping, active, created_at, updated_at
                        """,
                        payload.model_dump(mode="json"),
                    )
                except Exception as exc:
                    raise HTTPException(status_code=409, detail=f"Não foi possível criar o sistema: {exc}") from exc
                row = await cur.fetchone()

            await record_audit(
                conn,
                sistema=payload.codigo,
                evento_tipo="config_sistema_criado",
                detalhe={"nome": payload.nome, "base_url": payload.base_url},
            )
    return SystemOut(**row)


@router.patch("/{codigo}", response_model=SystemOut)
async def update_system(codigo: str, payload: SystemUpdate) -> SystemOut:
    updates = {k: v for k, v in payload.model_dump(mode="json", exclude_unset=True).items()}
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar.")

    set_clause = ", ".join(f"{campo} = %({campo})s" for campo in updates)
    updates["codigo"] = codigo

    async with get_connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE systems SET {set_clause}
                    WHERE codigo = %(codigo)s
                    RETURNING codigo, nome, base_url, auth_type, status_mapping, active, created_at, updated_at
                    """,
                    updates,
                )
                row = await cur.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="Sistema não encontrado.")

            await record_audit(
                conn,
                sistema=codigo,
                evento_tipo="config_sistema_alterado",
                detalhe={"campos_alterados": list(payload.model_dump(exclude_unset=True).keys())},
            )
    return SystemOut(**row)


async def _fetch_one(codigo: str) -> dict | None:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT codigo, nome, base_url, auth_type, status_mapping, active, created_at, updated_at "
                "FROM systems WHERE codigo = %(codigo)s",
                {"codigo": codigo},
            )
            return await cur.fetchone()
