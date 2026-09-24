"""
store.py
--------
SQLite persistence for the simulator: its configuration, its local
tickets, and each ticket's message timeline (everything sent to or
received from the bridge). Plain sqlite3 with a connection per call -
the simulator is a single-user dev tool, so there's nothing to pool.

A ticket's `ref` (e.g. SIM-0001) is this system's own ticket ID - the
`external_ref` it sends to the bridge. `linked` records whether the
bridge knows that ref yet: a ticket received as `ticket.created` isn't
linked until this system first posts an event on its conversation, and
until then the bridge keeps sending `ticket.created` for it.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ref             TEXT UNIQUE,
    conversation_id TEXT UNIQUE,
    subject         TEXT,
    topic_code      TEXT,
    status          TEXT NOT NULL,
    origin          TEXT NOT NULL CHECK (origin IN ('sent', 'received')),
    counterpart     TEXT,
    linked          INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    direction   TEXT NOT NULL CHECK (direction IN ('out', 'in')),
    event       TEXT,
    status      TEXT NOT NULL,
    system      TEXT,
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
"""

# Settings keys, with the environment variable that seeds each one on
# first use. Once saved from the Config page, the stored value wins.
SETTINGS_ENV = {
    "bridge_url": ("SIM_BRIDGE_URL", "http://localhost:8080"),
    "system_code": ("SIM_SYSTEM_CODE", "simulator"),
    "api_key": ("SIM_API_KEY", ""),
    "ref_prefix": ("SIM_REF_PREFIX", "SIM"),
    "default_topic": ("SIM_DEFAULT_TOPIC", ""),
    "inbound_header": ("SIM_INBOUND_HEADER", "X-API-Key"),
    "inbound_secret": ("SIM_INBOUND_SECRET", ""),
}
SECRET_SETTINGS = {"api_key", "inbound_secret"}


def _db_path() -> Path:
    return Path(os.environ.get("SIM_DATA_DIR", "data")) / "simulator.db"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:  # commits on success, rolls back on exception
            yield conn
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# --- settings ---------------------------------------------------------------

def get_settings() -> dict[str, str]:
    with connect() as conn:
        stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    return {
        key: stored.get(key, os.environ.get(env, default))
        for key, (env, default) in SETTINGS_ENV.items()
    }


def save_settings(values: dict[str, str]) -> None:
    with connect() as conn:
        for key, value in values.items():
            if key not in SETTINGS_ENV:
                continue
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


# --- tickets ----------------------------------------------------------------

def _ticket(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    ticket = dict(row)
    ticket["linked"] = bool(ticket["linked"])
    return ticket


def list_tickets() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM tickets ORDER BY updated_at DESC, id DESC").fetchall()
    return [_ticket(r) for r in rows]


def get_ticket(ticket_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        return _ticket(conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone())


def find_ticket(*, ref: str | None = None, conversation_id: str | None = None) -> dict[str, Any] | None:
    with connect() as conn:
        row = None
        if ref:
            row = conn.execute("SELECT * FROM tickets WHERE ref = ?", (ref,)).fetchone()
        if row is None and conversation_id:
            row = conn.execute(
                "SELECT * FROM tickets WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
    return _ticket(row)


def create_ticket(
    *, ref_prefix: str, subject: str | None, topic_code: str | None, status: str,
    origin: str, conversation_id: str | None = None, counterpart: str | None = None,
) -> dict[str, Any]:
    """Inserts a ticket and assigns its ref from the row id (e.g. SIM-0001)."""
    ts = now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO tickets (subject, topic_code, status, origin, conversation_id, "
            "counterpart, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (subject, topic_code, status, origin, conversation_id, counterpart, ts, ts),
        )
        ref = f"{ref_prefix}-{cur.lastrowid:04d}"
        conn.execute("UPDATE tickets SET ref = ? WHERE id = ?", (ref, cur.lastrowid))
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _ticket(row)


def delete_ticket(ticket_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))


def update_ticket(ticket_id: int, **fields: Any) -> None:
    allowed = {"status", "conversation_id", "linked", "counterpart", "subject"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    fields["updated_at"] = now()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE tickets SET {assignments} WHERE id = ?", (*fields.values(), ticket_id))


# --- messages ---------------------------------------------------------------

def add_message(
    ticket_id: int, *, direction: str, status: str, system: str | None,
    metadata: dict[str, Any], event: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO messages (ticket_id, direction, event, status, system, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, direction, event, status, system, json.dumps(metadata), now()),
        )


def list_messages(ticket_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE ticket_id = ? ORDER BY id", (ticket_id,)
        ).fetchall()
    return [{**dict(r), "metadata": json.loads(r["metadata"])} for r in rows]
