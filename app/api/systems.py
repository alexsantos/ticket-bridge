"""
systems.py
----------
CRUD for federated system configuration (/api/v1/systems).

Used by the configuration frontend so that adding/changing/disabling a
system (the "third system" from the original design) is a runtime
configuration operation, without deploying new code.

Security note: these endpoints should sit behind Cloud Run IAM
authentication or an authentication proxy - they have no authentication of
their own because access is assumed to be limited to administrators (see
README.md).
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
                "SELECT code, name, base_url, auth_type, status_mapping, active, created_at, updated_at "
                "FROM systems ORDER BY code"
            )
            rows = await cur.fetchall()
    return [SystemOut(**row) for row in rows]


@router.get("/{code}", response_model=SystemOut)
async def get_system(code: str) -> SystemOut:
    row = await _fetch_one(code)
    if row is None:
        raise HTTPException(status_code=404, detail="System not found.")
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
                            (code, name, base_url, auth_type, auth_config, status_mapping, payload_template, active)
                        VALUES
                            (%(code)s, %(name)s, %(base_url)s, %(auth_type)s, %(auth_config)s,
                             %(status_mapping)s, %(payload_template)s, %(active)s)
                        RETURNING code, name, base_url, auth_type, status_mapping, active, created_at, updated_at
                        """,
                        payload.model_dump(mode="json"),
                    )
                except Exception as exc:
                    raise HTTPException(status_code=409, detail=f"Could not create system: {exc}") from exc
                row = await cur.fetchone()

            await record_audit(
                conn,
                system_code=payload.code,
                event_type="system_config_created",
                detail={"name": payload.name, "base_url": payload.base_url},
            )
    return SystemOut(**row)


@router.patch("/{code}", response_model=SystemOut)
async def update_system(code: str, payload: SystemUpdate) -> SystemOut:
    updates = {k: v for k, v in payload.model_dump(mode="json", exclude_unset=True).items()}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    set_clause = ", ".join(f"{field} = %({field})s" for field in updates)
    updates["code"] = code

    async with get_connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE systems SET {set_clause}
                    WHERE code = %(code)s
                    RETURNING code, name, base_url, auth_type, status_mapping, active, created_at, updated_at
                    """,
                    updates,
                )
                row = await cur.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="System not found.")

            await record_audit(
                conn,
                system_code=code,
                event_type="system_config_updated",
                detail={"changed_fields": list(payload.model_dump(exclude_unset=True).keys())},
            )
    return SystemOut(**row)


async def _fetch_one(code: str) -> dict | None:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT code, name, base_url, auth_type, status_mapping, active, created_at, updated_at "
                "FROM systems WHERE code = %(code)s",
                {"code": code},
            )
            return await cur.fetchone()
