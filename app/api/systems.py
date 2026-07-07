"""
systems.py
----------
CRUD for federated system configuration (/api/v1/systems).

Used by the configuration frontend so that adding/changing/disabling a
system (the "third system" from the original design), or changing which
topics it subscribes to, is a runtime configuration operation, without
deploying new code.

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

# Shared read shape: every system row is enriched with its subscribed topic
# codes via a correlated subquery, so list/get/create/update all return the
# exact same fields without needing a GROUP BY.
_SELECT_SYSTEM_COLUMNS = """
    s.code, s.name, s.base_url, s.active,
    s.created_at, s.updated_at,
    COALESCE(
        (SELECT array_agg(topic_code ORDER BY topic_code)
         FROM system_topic_subscriptions WHERE system_code = s.code),
        ARRAY[]::text[]
    ) AS topics
"""


@router.get("", response_model=list[SystemOut])
async def list_systems() -> list[SystemOut]:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT {_SELECT_SYSTEM_COLUMNS} FROM systems s ORDER BY s.code")
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
                            (code, name, base_url, auth_config, active)
                        VALUES
                            (%(code)s, %(name)s, %(base_url)s, %(auth_config)s, %(active)s)
                        """,
                        payload.model_dump(mode="json"),
                    )
                    if payload.topics:
                        await cur.execute(
                            """
                            INSERT INTO system_topic_subscriptions (system_code, topic_code)
                            SELECT %(code)s, unnest(%(topics)s::text[])
                            """,
                            {"code": payload.code, "topics": payload.topics},
                        )
                except Exception as exc:
                    raise HTTPException(status_code=409, detail=f"Could not create system: {exc}") from exc

                await cur.execute(
                    f"SELECT {_SELECT_SYSTEM_COLUMNS} FROM systems s WHERE s.code = %(code)s",
                    {"code": payload.code},
                )
                row = await cur.fetchone()

            await record_audit(
                conn,
                system_code=payload.code,
                event_type="system_config_created",
                detail={"name": payload.name, "base_url": payload.base_url, "topics": payload.topics},
            )
    return SystemOut(**row)


@router.patch("/{code}", response_model=SystemOut)
async def update_system(code: str, payload: SystemUpdate) -> SystemOut:
    raw_updates = payload.model_dump(mode="json", exclude_unset=True)
    if not raw_updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    changed_fields = list(raw_updates.keys())
    topics_provided = "topics" in raw_updates
    topics = raw_updates.pop("topics", None)
    column_updates = raw_updates

    async with get_connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                if column_updates:
                    set_clause = ", ".join(f"{field} = %({field})s" for field in column_updates)
                    await cur.execute(
                        f"UPDATE systems SET {set_clause} WHERE code = %(code)s RETURNING code",
                        {**column_updates, "code": code},
                    )
                else:
                    await cur.execute("SELECT code FROM systems WHERE code = %(code)s", {"code": code})

                if await cur.fetchone() is None:
                    raise HTTPException(status_code=404, detail="System not found.")

                if topics_provided:
                    await cur.execute(
                        "DELETE FROM system_topic_subscriptions WHERE system_code = %(code)s", {"code": code}
                    )
                    if topics:
                        try:
                            await cur.execute(
                                """
                                INSERT INTO system_topic_subscriptions (system_code, topic_code)
                                SELECT %(code)s, unnest(%(topics)s::text[])
                                """,
                                {"code": code, "topics": topics},
                            )
                        except Exception as exc:
                            raise HTTPException(
                                status_code=409, detail=f"Could not set topic subscriptions: {exc}"
                            ) from exc

                await cur.execute(
                    f"SELECT {_SELECT_SYSTEM_COLUMNS} FROM systems s WHERE s.code = %(code)s", {"code": code}
                )
                row = await cur.fetchone()

            await record_audit(
                conn,
                system_code=code,
                event_type="system_config_updated",
                detail={"changed_fields": changed_fields},
            )
    return SystemOut(**row)


async def _fetch_one(code: str) -> dict | None:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_SELECT_SYSTEM_COLUMNS} FROM systems s WHERE s.code = %(code)s", {"code": code}
            )
            return await cur.fetchone()
