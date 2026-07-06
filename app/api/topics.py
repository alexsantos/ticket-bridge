"""
topics.py
---------
CRUD for ticket topics/categories (/api/v1/topics), e.g. INFRA, SPM, SALES.

Every conversation must declare an active topic on creation (see
app/api/events.py), and systems declare their subscriptions to topics via
`SystemCreate`/`SystemUpdate.topics` (see app/api/systems.py). This file
only manages the topics themselves - structurally a clone of
app/api/systems.py's CRUD pattern.

Security note: same as systems.py - no authentication of its own, assumed
to sit behind Cloud Run IAM or an authentication proxy (see README.md).
"""
from fastapi import APIRouter, HTTPException

from app.database import get_connection
from app.schemas import TopicCreate, TopicOut, TopicUpdate
from app.services.audit_service import record_audit

router = APIRouter(prefix="/api/v1/topics", tags=["topics"])


@router.get("", response_model=list[TopicOut])
async def list_topics() -> list[TopicOut]:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT code, name, description, active, created_at, updated_at FROM topics ORDER BY code"
            )
            rows = await cur.fetchall()
    return [TopicOut(**row) for row in rows]


@router.get("/{code}", response_model=TopicOut)
async def get_topic(code: str) -> TopicOut:
    row = await _fetch_one(code)
    if row is None:
        raise HTTPException(status_code=404, detail="Topic not found.")
    return TopicOut(**row)


@router.post("", response_model=TopicOut, status_code=201)
async def create_topic(payload: TopicCreate) -> TopicOut:
    async with get_connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        """
                        INSERT INTO topics (code, name, description, active)
                        VALUES (%(code)s, %(name)s, %(description)s, %(active)s)
                        RETURNING code, name, description, active, created_at, updated_at
                        """,
                        payload.model_dump(mode="json"),
                    )
                except Exception as exc:
                    raise HTTPException(status_code=409, detail=f"Could not create topic: {exc}") from exc
                row = await cur.fetchone()

            await record_audit(
                conn,
                system_code=None,
                event_type="topic_config_created",
                detail={"code": payload.code, "name": payload.name},
            )
    return TopicOut(**row)


@router.patch("/{code}", response_model=TopicOut)
async def update_topic(code: str, payload: TopicUpdate) -> TopicOut:
    updates = payload.model_dump(mode="json", exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    set_clause = ", ".join(f"{field} = %({field})s" for field in updates)
    updates["code"] = code

    async with get_connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE topics SET {set_clause}
                    WHERE code = %(code)s
                    RETURNING code, name, description, active, created_at, updated_at
                    """,
                    updates,
                )
                row = await cur.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="Topic not found.")

            await record_audit(
                conn,
                system_code=None,
                event_type="topic_config_updated",
                detail={"code": code, "changed_fields": list(payload.model_dump(exclude_unset=True).keys())},
            )
    return TopicOut(**row)


async def _fetch_one(code: str) -> dict | None:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT code, name, description, active, created_at, updated_at FROM topics WHERE code = %(code)s",
                {"code": code},
            )
            return await cur.fetchone()
