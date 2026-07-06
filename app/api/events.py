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
  2. Creates or locates the conversation (correlation_service).
  3. Compares the new status with the last known one for this pair
     (conversation, source system) - only proceeds if there's an actual
     change (basic protection against resends/echo).
  4. Performs fan-out: for each other participant in the conversation (or
     the explicit `recipients` list), translates the status to that
     system's vocabulary and inserts a row into the outbox.
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
            # 1. Correlation: create or locate the conversation and update the source participant.
            try:
                conversation_id, created = await correlation_service.find_or_create_conversation(
                    conn,
                    conversation_id=event.conversation_id,
                    source=source,
                    external_ref=event.external_ref,
                    status=event.status,
                    subject=event.subject,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            await record_audit(
                conn,
                conversation_id=conversation_id,
                system_code=source,
                event_type="event_received",
                detail=event.model_dump(mode="json"),
            )

            # 2. Determine the fan-out recipients.
            others = await correlation_service.list_other_participants(
                conn, conversation_id=conversation_id, exclude=source
            )
            allowed_destinations = set(event.recipients) if event.recipients else None
            outbox_ids: list[int] = []

            for participant in others:
                destination = participant["system_code"]
                if allowed_destinations is not None and destination not in allowed_destinations:
                    continue

                destination_system = await _get_system_config(conn, destination)
                if destination_system is None or not destination_system["active"]:
                    logger.warning("Destination '%s' inactive or nonexistent - skipping.", destination)
                    continue

                translated_status = status_mapper.internal_to_external(
                    destination_system["status_mapping"], event.status
                )

                payload = _build_payload(
                    template=destination_system["payload_template"],
                    external_ref=participant["external_ref"],
                    mapped_status=translated_status,
                    conversation_id=conversation_id,
                )

                outbox_id = await outbox_service.enqueue(
                    conn,
                    conversation_id=conversation_id,
                    destination=destination,
                    source=source,
                    payload=payload,
                )
                outbox_ids.append(outbox_id)

            await record_audit(
                conn,
                conversation_id=conversation_id,
                system_code=source,
                event_type="fanout_scheduled",
                detail={"outbox_ids": outbox_ids, "destinations": [o["system_code"] for o in others]},
            )

    return IncomingEventResponse(conversation_id=conversation_id, created_outbox_ids=outbox_ids)


async def _get_system_config(conn, code: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT code, base_url, auth_type, auth_config, status_mapping, payload_template, active "
            "FROM systems WHERE code = %(code)s",
            {"code": code},
        )
        return await cur.fetchone()


def _build_payload(*, template: dict, external_ref: str, mapped_status: str, conversation_id: UUID) -> dict:
    """
    Applies simple placeholder substitution for {external_ref},
    {mapped_status} and {conversation_id} in the string values of the
    template configured for the destination system (systems.payload_template).
    """
    values = {
        "external_ref": external_ref,
        "mapped_status": mapped_status,
        "conversation_id": str(conversation_id),
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
    return _resolve(template)
