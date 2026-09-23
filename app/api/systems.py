"""
systems.py
----------
CRUD for federated system configuration (/api/v1/systems), plus
lifecycle management of each system's inbound API keys
(/api/v1/systems/{code}/api-keys) - the credential that system sends as
X-API-Key on POST /api/v1/events (see app/security.py).

Used by the configuration frontend so that adding/changing/disabling a
system (the "third system" from the original design), or changing which
topics it subscribes to, is a runtime configuration operation, without
deploying new code.

Security note: every route here requires an authenticated admin session
(see require_login in app/security.py, app/api/auth.py, CLAUDE.md
Decision 11) - no longer assumed to sit behind an external proxy.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.database import get_connection
from app.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut, SystemCreate, SystemOut, SystemUpdate
from app.security import generate_api_key, require_login
from app.services.audit_service import record_audit

router = APIRouter(prefix="/api/v1/systems", tags=["systems"], dependencies=[Depends(require_login)])

# Shared read shape: every system row is enriched with its subscribed topic
# codes via a correlated subquery, so list/get/create/update all return the
# exact same fields without needing a GROUP BY. auth_config is included here
# so _row_to_system_out can split it into its non-secret parts - it is never
# returned as-is (see SystemOut).
_SELECT_SYSTEM_COLUMNS = """
    s.code, s.name, s.base_url, s.active, s.auth_config,
    s.created_at, s.updated_at,
    COALESCE(
        (SELECT array_agg(topic_code ORDER BY topic_code)
         FROM system_topic_subscriptions WHERE system_code = s.code),
        ARRAY[]::text[]
    ) AS topics
"""


def _row_to_system_out(row: dict) -> SystemOut:
    row = dict(row)
    auth_config = row.pop("auth_config", None) or {}
    return SystemOut(
        **row,
        auth_header=auth_config.get("header"),
        auth_value_prefix=auth_config.get("value_prefix"),
        has_secret=bool(auth_config.get("secret_ref")),
    )


@router.get("", response_model=list[SystemOut])
async def list_systems() -> list[SystemOut]:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT {_SELECT_SYSTEM_COLUMNS} FROM systems s ORDER BY s.code")
            rows = await cur.fetchall()
    return [_row_to_system_out(row) for row in rows]


@router.get("/{code}", response_model=SystemOut)
async def get_system(code: str) -> SystemOut:
    row = await _fetch_one(code)
    if row is None:
        raise HTTPException(status_code=404, detail="System not found.")
    return _row_to_system_out(row)


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
    return _row_to_system_out(row)


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
                    # auth_config is merged (jsonb ||), not replaced: the frontend only
                    # ever sends the auth_config keys the admin actually typed a new
                    # value for (see app.js) - a shallow replace would silently wipe
                    # out, e.g., an existing secret_ref whenever only the header or
                    # value_prefix was being changed, since SystemOut never returns
                    # secret_ref for the frontend to send back unchanged.
                    set_parts = [
                        "auth_config = COALESCE(auth_config, '{}'::jsonb) || %(auth_config)s::jsonb"
                        if field == "auth_config" else f"{field} = %({field})s"
                        for field in column_updates
                    ]
                    set_clause = ", ".join(set_parts)
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
    return _row_to_system_out(row)


@router.get("/{code}/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(code: str) -> list[ApiKeyOut]:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM systems WHERE code = %(code)s", {"code": code})
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="System not found.")

            await cur.execute(
                """
                SELECT id, description, active, created_at, revoked_at
                FROM api_keys
                WHERE system_code = %(code)s
                ORDER BY created_at DESC
                """,
                {"code": code},
            )
            rows = await cur.fetchall()
    return [ApiKeyOut(**row) for row in rows]


@router.post("/{code}/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(code: str, payload: ApiKeyCreate) -> ApiKeyCreated:
    """
    Generates a new inbound API key for this system - the credential it
    sends as X-API-Key on POST /api/v1/events. The plaintext key is only
    ever present in this response; only its hash is persisted.
    """
    raw_key, key_hash = generate_api_key()

    async with get_connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1 FROM systems WHERE code = %(code)s", {"code": code})
                if await cur.fetchone() is None:
                    raise HTTPException(status_code=404, detail="System not found.")

                await cur.execute(
                    """
                    INSERT INTO api_keys (system_code, key_hash, description)
                    VALUES (%(code)s, %(hash)s, %(description)s)
                    RETURNING id, description, created_at
                    """,
                    {"code": code, "hash": key_hash, "description": payload.description},
                )
                row = await cur.fetchone()

            await record_audit(
                conn,
                system_code=code,
                event_type="inbound_api_key_created",
                detail={"api_key_id": row["id"], "description": payload.description},
            )
    return ApiKeyCreated(**row, api_key=raw_key)


@router.delete("/{code}/api-keys/{key_id}", status_code=204)
async def revoke_api_key(code: str, key_id: int) -> None:
    """Revokes an inbound API key. Revocation is immediate and irreversible - a new key must be issued if the system still needs access."""
    async with get_connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE api_keys SET active = FALSE, revoked_at = now()
                    WHERE id = %(id)s AND system_code = %(code)s AND active = TRUE
                    RETURNING id
                    """,
                    {"id": key_id, "code": code},
                )
                if await cur.fetchone() is None:
                    raise HTTPException(status_code=404, detail="Active API key not found for this system.")

            await record_audit(
                conn,
                system_code=code,
                event_type="inbound_api_key_revoked",
                detail={"api_key_id": key_id},
            )


async def _fetch_one(code: str) -> dict | None:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_SELECT_SYSTEM_COLUMNS} FROM systems s WHERE s.code = %(code)s", {"code": code}
            )
            return await cur.fetchone()
