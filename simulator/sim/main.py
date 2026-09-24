"""
main.py
-------
FastAPI app for the simulator:

  POST /webhook              where the bridge delivers ticket events (set
                             this as the system's base_url in the bridge)
  /api/tickets...            the frontend's ticket list, detail, create and
                             reply - create/reply call the bridge's
                             POST /api/v1/events
  /api/config...             the Config page
  /                          the static frontend (sim/static)

Endpoints are plain `def` (FastAPI runs them in a threadpool): sqlite3
and the httpx calls to the bridge are synchronous, and a single-user dev
tool doesn't need more.
"""
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sim import bridge, store
from sim.contract import InboundTicketEvent, Status


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init()
    yield


app = FastAPI(title="Ticket Bridge Simulator", lifespan=lifespan)


# --- webhook (bridge -> simulator) ------------------------------------------

@app.post("/webhook")
def webhook(event: InboundTicketEvent, request: Request) -> dict[str, Any]:
    settings = store.get_settings()
    _check_inbound_auth(settings, request.headers.get(settings["inbound_header"] or "X-API-Key"))
    return _receive(settings, event)


def _check_inbound_auth(settings: dict[str, str], presented: str | None) -> None:
    """
    If an expected value is configured, the header must match it exactly -
    including any prefix the bridge adds (auth_config.value_prefix, e.g.
    "Bearer "). A 401 here shows up in the bridge as a delivery failure,
    which is the point: it lets you test the bridge's outbound auth config.
    """
    expected = settings["inbound_secret"]
    if expected and not (presented and hmac.compare_digest(presented, expected)):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook credential.")


def _receive(settings: dict[str, str], event: InboundTicketEvent) -> dict[str, Any]:
    # Match on our own ref first, then on the conversation: until this
    # system replies, the bridge doesn't know our ref and keeps sending
    # `ticket.created` (with no external_ref) for the same conversation.
    ticket = store.find_ticket(ref=event.external_ref, conversation_id=event.conversation_id)
    if ticket is None:
        ticket = store.create_ticket(
            ref_prefix=settings["ref_prefix"] or "SIM",
            subject=event.conversation_subject,
            topic_code=None,  # not part of the outbound payload
            status=event.status,
            origin="received",
            conversation_id=event.conversation_id,
            counterpart=event.source_system,
        )
    else:
        store.update_ticket(ticket["id"], status=event.status, counterpart=event.source_system)

    store.add_message(
        ticket["id"], direction="in", event=event.event, status=event.status,
        system=event.source_system, metadata=event.metadata,
    )
    return {"received": True, "ref": ticket["ref"]}


# --- tickets (frontend -> simulator -> bridge) ------------------------------

class NewTicket(BaseModel):
    topic_code: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    status: Status = "new"
    note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Reply(BaseModel):
    status: Status
    note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _with_note(metadata: dict[str, Any], note: str | None) -> dict[str, Any]:
    return {**metadata, "note": note} if note else dict(metadata)


@app.get("/api/tickets")
def list_tickets() -> list[dict[str, Any]]:
    return store.list_tickets()


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: int) -> dict[str, Any]:
    ticket = store.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    return {**ticket, "messages": store.list_messages(ticket_id)}


@app.post("/api/tickets", status_code=201)
def create_ticket(payload: NewTicket) -> dict[str, Any]:
    settings = store.get_settings()
    metadata = _with_note(payload.metadata, payload.note)
    ticket = store.create_ticket(
        ref_prefix=settings["ref_prefix"] or "SIM", subject=payload.subject,
        topic_code=payload.topic_code, status=payload.status, origin="sent",
    )
    try:
        result = bridge.post_event(settings, {
            "external_ref": ticket["ref"], "status": payload.status, "subject": payload.subject,
            "topic_code": payload.topic_code, "metadata": metadata,
        })
    except bridge.BridgeError as exc:
        store.delete_ticket(ticket["id"])  # the bridge never saw it - don't keep a ghost
        raise HTTPException(status_code=502, detail=exc.detail) from exc

    store.update_ticket(ticket["id"], conversation_id=result["conversation_id"], linked=1)
    store.add_message(ticket["id"], direction="out", status=payload.status,
                      system=settings["system_code"], metadata=metadata)
    return get_ticket(ticket["id"])


@app.post("/api/tickets/{ticket_id}/reply")
def reply(ticket_id: int, payload: Reply) -> dict[str, Any]:
    settings = store.get_settings()
    ticket = store.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    metadata = _with_note(payload.metadata, payload.note)
    try:
        bridge.post_event(settings, {
            "conversation_id": ticket["conversation_id"], "external_ref": ticket["ref"],
            "status": payload.status, "metadata": metadata,
        })
    except bridge.BridgeError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc

    # The bridge now has our ref for this conversation, so later deliveries
    # arrive as `ticket.updated`.
    store.update_ticket(ticket_id, status=payload.status, linked=1)
    store.add_message(ticket_id, direction="out", status=payload.status,
                      system=settings["system_code"], metadata=metadata)
    return get_ticket(ticket_id)


# --- config -----------------------------------------------------------------

class ConfigUpdate(BaseModel):
    bridge_url: str = Field(min_length=1)
    system_code: str = ""
    ref_prefix: str = Field(default="SIM", min_length=1)
    default_topic: str = ""
    inbound_header: str = "X-API-Key"
    # Secrets: None or "" keeps the stored value; the clear_* flags remove it.
    api_key: str | None = None
    inbound_secret: str | None = None
    clear_inbound_secret: bool = False


def _public_config() -> dict[str, Any]:
    """Secrets are never sent back to the browser - only whether they're set."""
    settings = store.get_settings()
    public = {k: v for k, v in settings.items() if k not in store.SECRET_SETTINGS}
    public.update({f"{k}_set": bool(settings[k]) for k in store.SECRET_SETTINGS})
    return public


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return _public_config()


@app.put("/api/config")
def put_config(payload: ConfigUpdate) -> dict[str, Any]:
    values = payload.model_dump(exclude={"api_key", "inbound_secret", "clear_inbound_secret"})
    if payload.api_key:
        values["api_key"] = payload.api_key
    if payload.clear_inbound_secret:
        values["inbound_secret"] = ""
    elif payload.inbound_secret:
        values["inbound_secret"] = payload.inbound_secret
    store.save_settings(values)
    return _public_config()


@app.post("/api/config/test")
def test_config() -> dict[str, str]:
    try:
        return {"result": bridge.check_health(store.get_settings())}
    except bridge.BridgeError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
