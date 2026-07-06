# CLAUDE.md — Ticket Bridge

This file is the source of truth for **why** the system is designed the
way it is. It exists to regain context in future sessions (human or
AI-assisted) without repeating the architecture discussion from scratch.

## Original problem

Two teams used OSTicket as a shared system: one team creates a ticket for
the other, and both track its status in the same place. OSTicket is being
decommissioned. Each team will move to its own operational support
application (different from each other), and the ability to correlate and
synchronize the state of a "process" across the two (and potentially more)
systems needs to be preserved.

## Decision 1 — Central bridge, not direct point-to-point integration

**Rejected alternative**: each system exposes an API and calls the other's
API directly.

**Why it was rejected**:
- Couples the two systems to each other's contract; changing one's schema
  forces the other to react.
- Doesn't scale to N systems — direct integration is O(N²).
- Without a single owner of correlation, there's no central place for
  auditing, reprocessing, or diagnosing a sync that fails midway.
- Risk of a notification loop (A updates → notifies B → B updates →
  notifies A → ...) with no natural cutoff.

**Decision made**: a neutral bridge service, with its own correlation
table (`conversations` / `conversation_participants`) that doesn't belong
to either system. This lightly recreates the "source-of-truth hub" role
that OSTicket implicitly played — but now explicit, federated, and
independent of the number of systems involved.

## Decision 2 — Outbox pattern on Postgres, not RabbitMQ/Pub-Sub

**Rejected alternative**: a dedicated message queue (RabbitMQ, as used in
other projects by the author, or Cloud Pub/Sub).

**Why it was rejected for this specific case**:
- The explicit requirement was "a very lightweight system that can run on
  Cloud Run alone." RabbitMQ assumes an always-on consumer, which doesn't
  fit Cloud Run scaling to zero between traffic spikes.
- An external broker is one more component to operate, upgrade, and
  monitor for an event volume that is, by nature, low (ticket status
  changes between two teams, not a high-throughput pipeline).

**Decision made**: the `outbox` table works as the queue. Writing the
business event (conversation/participant) and inserting into the queue
happen in the **same Postgres transaction**, eliminating the "dual write"
problem that would exist with a separate external broker. `SELECT ... FOR
UPDATE SKIP LOCKED` guarantees that multiple concurrent invocations of the
`/sync` endpoint never process the same row twice, with no external
coordination.

**Consciously accepted trade-off**: synchronization latency on the order of
minutes (Cloud Scheduler's cadence), not seconds. For synchronizing ticket
state between teams — not for real-time clinical events — this is
adequate. If the latency requirement changes in the future, the natural
migration would be to replace Cloud Scheduler with Pub/Sub push, keeping
the `outbox` table as an audit/replay log.

## Decision 3 — Support for N systems from the start, not just A/B

When the question "what if a third application shows up?" came up, the
answer wasn't "add more columns" but to generalize the model:
- `conversation_participants` is an associative table (conversation ↔
  system), not fixed `system_a_ref` / `system_b_ref` columns.
- The outbox fan-out on insert is "every participant of the conversation
  except the source," not "the other side of the pair."
- Each system's configuration (`systems`) includes everything specific to
  it (URL, authentication, status mapping, payload template), so that
  adding a new system is a configuration operation via the frontend, not a
  code change.

Cost of this decision: one extra table and one extra FK, essentially nil.
Benefit: avoids rewriting the data model when a 3rd or 4th system shows up
— which, given the author's integration history (Mirth Connect linking
multiple clinical systems), was a realistic scenario, not a hypothetical
one.

## Decision 4 — Internal status vocabulary + per-system mapping

Each external system has its own status vocabulary (e.g. "Open" vs "NEW").
Instead of the bridge knowing every vocabulary of every system in code,
each system defines a `status_mapping` (`{internal: external}`) in its
configuration. The bridge only knows the canonical internal vocabulary
(`new`, `in_progress`, `waiting_third_party`, `resolved`, `closed` — see
`status_mapper.py`).

This means the translation logic never grows with the number of systems:
the configuration grows, not the code.

## Decision 5 — Loop prevention

Each `outbox` row explicitly records `source` (who generated the event)
and `destination` (who it's going to). The fan-out when processing a
received event always excludes the source from the list of destinations.
This alone prevents the immediate echo (A→B→A in the same operation).

**Note for future evolution**: the current skeleton does not yet implement
deduplication of semantically identical events coming from different
sources within short time windows (e.g. both systems updating the same
field almost simultaneously). If this becomes a real problem in
production, the natural place to solve it is
`correlation_service.find_or_create_conversation`, comparing the already
stored `local_status` against the new one before generating fan-out.

## Decision 6 — No ORM

Database interaction uses explicit SQL via asynchronous `psycopg3`, not
SQLAlchemy or another ORM. With 6 tables and relatively simple queries, an
ORM would add a layer of abstraction with no real benefit, and the
concurrency logic (`FOR UPDATE SKIP LOCKED`) is more direct to write and
reason about in plain SQL than through ORM abstractions.

## Decision 7 — Frameworkless frontend

The configuration/audit frontend is vanilla HTML + CSS + JavaScript,
served as static files by FastAPI itself (`StaticFiles`). There is no
build step (webpack/vite/etc.). Rationale: this is a low-traffic internal
admin panel, not an end-user application — the maintenance cost of a build
pipeline isn't justified by the gain.

## What this skeleton assumes and leaves undecided

- **Human authentication for the frontend**: the code does not implement
  login; it assumes IAP or an authentication proxy in front of Cloud Run
  (see README.md, section 5). Deliberately deferred decision — it depends
  on how the organization already manages access to internal tools.
- **Field-conflict reconciliation** (who "wins" when both systems write the
  same field almost simultaneously): not implemented. The skeleton assumes
  each system is authoritative over its own `local_status`, and a
  conversation's `overall_status` is informational, not a normative source
  of truth. If an explicit per-direction field owner is needed (as
  initially discussed), the place to extend is `correlation_service.py`.
- **Failure alerts** (e.g. Telegram, as used in other projects by the
  author): not implemented in this skeleton; the natural place is inside
  `outbox_service.mark_failed`, when an entry reaches `max_attempts`.

## Code conventions

- Identifiers (function, variable, table, column names), comments,
  docstrings, and user-facing text (frontend, error messages) are all in
  English.
- Each `.py` file has a module docstring at the top explaining its
  responsibility — reading those docstrings is the fastest way to
  navigate the project for the first time.
- `app/services/` holds pure business logic, with no dependency on
  FastAPI; `app/api/` holds thin HTTP orchestration on top of the
  services.
