"""
events.py
---------
POST /api/v1/events

Single entry point for external systems to communicate the creation or
status change of a ticket. Replaces manual ticket creation/update in
OSTicket.

Flow (all within a single transaction):
  1. Authenticates the calling system via API key (`authenticate_system`
     dependency).
  2. If creating a new conversation, validates the requested topic exists,
     is active, and that the source system is subscribed to it (a system
     may not open a ticket under a topic it doesn't itself subscribe to).
  3. Creates or locates the conversation (correlation_service).
  4. Performs fan-out: every system currently subscribed to the
     conversation's topic (except the source, and narrowed by the explicit
     `recipients` list if provided) gets an outbox entry. Destinations with
     no prior `conversation_participants` row are told to open a new
     ticket rather than update one (see `_build_payload`).
  5. Records everything in audit_log.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.database import get_connection
from app.schemas import IncomingEvent, IncomingEventResponse
from app.security import authenticate_system
from app.services import correlation_service, outbox_service, status_mapper
from app.services.audit_service import record_audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.post("", response_model=IncomingEventResponse)
async def receive_event(
    event: IncomingEvent,
    source: str = Depends(authenticate_system),
) -> IncomingEventResponse:
    async with get_connection() as conn:
        async with conn.transaction():
            if event.conversation_id is None:
                await _validate_new_conversation_topic(conn, source=source, topic_code=event.topic_code)

            # 1. Correlation: create or locate the conversation and update the source participant.
            try:
                conversation_id, created, topic_code = await correlation_service.find_or_create_conversation(
                    conn,
                    conversation_id=event.conversation_id,
                    source=source,
                    external_ref=event.external_ref,
                    status=event.status,
                    subject=event.subject,
                    topic_code=event.topic_code,
                )
            except correlation_service.ConversationNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except correlation_service.TopicMismatch as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

            await record_audit(
                conn,
                conversation_id=conversation_id,
                system_code=source,
                event_type="event_received",
                detail=event.model_dump(mode="json"),
            )

            # 2. Determine the fan-out destinations: every system subscribed to this topic, except the source.
            destinations = await correlation_service.list_fanout_destinations(
                conn, conversation_id=conversation_id, topic_code=topic_code, exclude=source
            )
            allowed_destinations = set(event.recipients) if event.recipients else None
            outbox_ids: list[int] = []
            fanout_detail: list[dict] = []

            for destination_row in destinations:
                destination = destination_row["system_code"]
                if allowed_destinations is not None and destination not in allowed_destinations:
                    continue

                destination_system = await _get_system_config(conn, destination)
                if destination_system is None or not destination_system["active"]:
                    logger.warning("Destination '%s' inactive or nonexistent - skipping.", destination)
                    continue

                translated_status = status_mapper.internal_to_external(
                    destination_system["status_mapping"], event.status
                )
                is_known = destination_row["is_known_participant"]

                payload = _build_payload(
                    template=destination_system["payload_template"],
                    external_ref=destination_row["external_ref"],
                    mapped_status=translated_status,
                    conversation_id=conversation_id,
                    source_ref=event.external_ref,
                    source_system=source,
                    is_known=is_known,
                )

                outbox_id = await outbox_service.enqueue(
                    conn,
                    conversation_id=conversation_id,
                    destination=destination,
                    source=source,
                    payload=payload,
                )
                outbox_ids.append(outbox_id)
                fanout_detail.append({
                    "destination": destination,
                    "mode": "update" if is_known else "create",
                })

            await record_audit(
                conn,
                conversation_id=conversation_id,
                system_code=source,
                event_type="fanout_scheduled",
                detail={"topic_code": topic_code, "outbox_ids": outbox_ids, "destinations": fanout_detail},
            )

    return IncomingEventResponse(conversation_id=conversation_id, created_outbox_ids=outbox_ids)


async def _validate_new_conversation_topic(conn, *, source: str, topic_code: str | None) -> None:
    """Enforces the rules for creating a brand-new conversation: a valid, active topic that the source subscribes to."""
    if topic_code is None:
        raise HTTPException(status_code=422, detail="topic_code is required when creating a new conversation.")

    topic = await _get_topic(conn, topic_code)
    if topic is None:
        raise HTTPException(status_code=404, detail=f"Topic '{topic_code}' not found.")
    if not topic["active"]:
        raise HTTPException(status_code=409, detail=f"Topic '{topic_code}' is inactive.")

    subscribed = await correlation_service.is_system_subscribed(conn, system_code=source, topic_code=topic_code)
    if not subscribed:
        raise HTTPException(
            status_code=403,
            detail=f"System '{source}' must be subscribed to topic '{topic_code}' to create a ticket in it.",
        )


async def _get_topic(conn, code: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute("SELECT code, active FROM topics WHERE code = %(code)s", {"code": code})
        return await cur.fetchone()


async def _get_system_config(conn, code: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT code, base_url, auth_type, auth_config, status_mapping, payload_template, active "
            "FROM systems WHERE code = %(code)s",
            {"code": code},
        )
        return await cur.fetchone()


def _build_payload(
    *,
    template: dict,
    external_ref: str | None,
    mapped_status: str,
    conversation_id: UUID,
    source_ref: str,
    source_system: str,
    is_known: bool,
) -> dict:
    """
    Applies placeholder substitution to the template configured for the
    destination system (systems.payload_template).

    Available placeholders: {external_ref} (the destination's own known
    ticket ref - empty string if it doesn't have one yet), {mapped_status},
    {conversation_id}, {source_ref} (the source system's own ref),
    {source_system}, {fanout_mode} ("update" or "create").

    A template may optionally provide two variants under the reserved
    top-level keys "on_create"/"on_update"; the matching one is picked
    based on whether the destination already has a linked ticket
    (`is_known`). Templates that don't use these reserved keys are resolved
    as a single flat shape, exactly as before this distinction existed.
    """
    values = {
        "external_ref": external_ref or "",
        "mapped_status": mapped_status,
        "conversation_id": str(conversation_id),
        "source_ref": source_ref,
        "source_system": source_system,
        "fanout_mode": "update" if is_known else "create",
    }

    def _resolve(v):
        if isinstance(v, str):
            return v.format(**values)
        if isinstance(v, dict):
            return {k: _resolve(val) for k, val in v.items()}
        return v

    if not template:
        # No template configured - use a reasonable generic shape.
        return values

    if "on_create" in template or "on_update" in template:
        variant = template.get("on_update" if is_known else "on_create", {})
        return _resolve(variant)

    return _resolve(template)
