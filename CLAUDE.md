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
minutes (originally Cloud Scheduler's cadence, now the in-process
scheduler's interval - see update below), not seconds. For synchronizing
ticket state between teams — not for real-time clinical events — this is
adequate. If the latency requirement changes in the future, the natural
migration would be to replace polling with Pub/Sub push, keeping the
`outbox` table as an audit/replay log.

**Update — in-process scheduler instead of Cloud Scheduler**: the original
rationale for driving `/sync` from an external pinger (Cloud Scheduler)
was specifically Cloud Run's scale-to-zero model — a Cloud Run instance
can't be relied on to keep a background thread alive between requests.
Once the deployment target changed to a continuously-running process (a
VM, a long-lived container), that constraint no longer applies, and the
external dependency became unnecessary complexity rather than a
requirement. `app/scheduler.py` now runs an in-process APScheduler
(`AsyncIOScheduler`) job on the same event loop as the rest of the app,
calling the same `sync_service.run_sync_batch()` the HTTP endpoint uses
(the endpoint was kept as a manual/on-demand trigger, not removed). The
outbox's `FOR UPDATE SKIP LOCKED` concurrency guarantee is what makes this
safe even if the app ever runs as multiple instances, each with its own
independent scheduler firing on its own timer — at worst this means
redundant polling queries across instances, never double-processing. If
this deployment ever moves back to something that scales to zero (Cloud
Run), `SYNC_SCHEDULER_ENABLED=false` reverts to the original
externally-triggered model with no code changes.

## Decision 3 — Support for N systems from the start, not just A/B

When the question "what if a third application shows up?" came up, the
answer wasn't "add more columns" but to generalize the model:
- `conversation_participants` is an associative table (conversation ↔
  system), not fixed `system_a_ref` / `system_b_ref` columns.
- The outbox fan-out on insert is "every participant of the conversation
  except the source," not "the other side of the pair."
- Each system's configuration (`systems`) includes everything specific to
  it (URL, authentication), so that adding a new system is a configuration
  operation via the frontend, not a code change. (Status vocabulary and
  payload shape are deliberately *not* part of this per-system
  configuration - see Decision 4.)

Cost of this decision: one extra table and one extra FK, essentially nil.
Benefit: avoids rewriting the data model when a 3rd or 4th system shows up
— which, given the author's integration history (Mirth Connect linking
multiple clinical systems), was a realistic scenario, not a hypothetical
one.

## Decision 4 — One canonical status vocabulary, enforced, not mapped

**Superseded approach**: the bridge originally let each system define its
own `status_mapping` (`{internal: external}`, e.g. `system_a` mapped
`new`→`Open`, `system_b` mapped `new`→`NEW`) and its own `payload_template`
(a JSON shape with placeholder substitution, optionally split into
`on_create`/`on_update` variants). The bridge adapted itself to each
destination's own vocabulary and format; adding a system meant configuring
translation rules, not writing code.

**Why it was reversed**: this flexibility recreated exactly the kind of
per-integration drift that a shared correlation hub is supposed to
eliminate. The author's prior experience with OSTicket was the opposite
model — one fixed set of concepts/statuses/formats that every integrating
team had to conform to, a "closed spec." Integrating teams generally
*prefer* a closed spec: it's a fixed contract to build against once, not a
configuration surface to keep in sync as the bridge's mapping/template
JSON evolves. It also closed a real, previously-undetected gap: inbound
`status` was never actually translated from a source system's own
vocabulary in the first place (`external_to_internal` existed but was
dead code — never called), so in practice every caller already had to send
canonical vocabulary for the system to behave correctly. The per-system
mapping was only ever doing half its intended job, silently.

**Decision made**: the bridge defines one canonical status vocabulary
(`new`, `in_progress`, `waiting_third_party`, `resolved`, `closed` — see
`CanonicalStatus` in `schemas.py`) and one fixed outbound payload shape
(`OutboundTicketEvent`, built by `payload_builder.build_outbound_payload`)
that every system receives identically. There is no per-system
`status_mapping` or `payload_template` anymore (removed by migration
`003_standardize_ticket_status.sql`, which also adds `CHECK` constraints
on `conversations.overall_status` and `conversation_participants.local_status`
so the vocabulary is enforced at the database level too, not just at the
API boundary via Pydantic's `CanonicalStatus` enum). Translating between
this canonical vocabulary and whatever a given system's own internal
states are is now that system's own adapter code's job entirely — the
bridge no longer knows or cares what "Open" or "NEW" mean to anyone.

**Note**: this is specifically about the *status vocabulary and payload
shape*. `external_ref` (each system's own ticket ID) intentionally stays
system-specific — there is no ID mapping today and none is proposed; the
two concepts are easy to conflate but are independent decisions.

This means the bridge itself never grows per-system special-casing: the
contract is the same size regardless of how many systems join. The cost
moved from "bridge configuration, per system" to "each system's own
integration code," which is exactly where the user wants it.

**Update — `metadata` passthrough**: the first version of this decision
also dropped `IncomingEvent.metadata` from the outbound contract entirely
(it was accepted on input but only ever recorded in `audit_log`, never
forwarded), reasoning that untyped passthrough would recreate the same
per-integration drift this decision removes. That reasoning held only as
long as no concrete use actually needed it. The flagship use case
(`examples/README.md` — a clinical system and a patient-registration/
insurance system exchanging updates on a single case almost like a chat)
made the gap obvious: `system_b` finds a patient's insurance number and
has no way to hand it to `system_a` — only `status` crosses the bridge,
never the actual result of the work. So `OutboundTicketEvent` now carries
`metadata` too, forwarded from `IncomingEvent.metadata` as-is, per event
(not accumulated across the conversation - each message's metadata stands
alone, same as `status` does). This keeps the *shape* fixed (every
destination still gets the same top-level fields, always) while letting
the *contents* of `metadata` be whatever the two teams on either side of a
given topic agree to put there - the bridge itself never interprets it.

**Where that agreement is recorded**: since the bridge deliberately does
not validate `metadata`'s contents, there has to be *somewhere* the two
integrating teams write down which keys they expect (e.g. `insurance_number`,
`note`) - otherwise it's tribal knowledge. That place is the topic's own
`description` field (`topics.description`, editable via the "Topics" tab
or `PATCH /api/v1/topics/{code}`, visible to any integrating team via
`GET /api/v1/topics`) - see the seeded `PATIENT_ADMIN` topic in
`002_seed_example.sql` for the convention. This is documentation, not
enforcement: nothing rejects a call that omits or misspells an expected
key. If that turns out not to be enough, the natural next step is a
per-topic JSON Schema column that the bridge actually validates `metadata`
against - deliberately not implemented yet, since it reintroduces a
configuration surface and should only be added once there's a concrete
need for it, the same reasoning `metadata` passthrough itself only
existed once a concrete need appeared.

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
SQLAlchemy or another ORM. With a handful of tables and relatively simple
queries, an ORM would add a layer of abstraction with no real benefit, and
the concurrency logic (`FOR UPDATE SKIP LOCKED`) is more direct to write
and reason about in plain SQL than through ORM abstractions.

## Decision 7 — Frameworkless frontend

The configuration/audit frontend is vanilla HTML + CSS + JavaScript,
served as static files by FastAPI itself (`StaticFiles`). There is no
build step (webpack/vite/etc.). Rationale: this is a low-traffic internal
admin panel, not an end-user application — the maintenance cost of a build
pipeline isn't justified by the gain.

## Decision 8 — Topics: mandatory ticket categorization + subscription-driven fan-out

**Problem closed**: Decision 3's fan-out only reaches systems that already
hold a `conversation_participants` row for a conversation — a brand-new
conversation notified nobody, because nobody had linked to it yet. A second
system had to learn the `conversation_id` out-of-band and call
`/api/v1/events` itself before it received anything. There was also no way
to route a ticket to only the systems actually interested in that kind of
work.

**Decision made**: every conversation must declare a `topic_code` (from a
`topics` lookup table, e.g. `INFRA`, `SPM`, `SALES`) at creation, immutable
afterward. Each system declares its own interests in
`system_topic_subscriptions`. Fan-out (`correlation_service.
list_fanout_destinations`) is now driven strictly by "is this system
currently subscribed to the conversation's topic" — not by prior
participant membership. The name **`topic`** (not `subject`) was chosen
deliberately to avoid colliding with the pre-existing free-text
`conversations.subject` column (the human-readable ticket title, left
untouched) — pub/sub terminology maps directly onto what this is.

**Consciously chosen trade-offs**:
- **A system must be subscribed to a topic to create a ticket under it.**
  Not enforced as a side effect — an explicit check in
  `app/api/events.py` before `find_or_create_conversation` runs.
- **Unsubscribing is immediate and absolute.** A system that removes its
  subscription to a topic stops receiving any further fan-out for that
  topic right away, even for conversations it already has an open ticket
  on — there is no "grandfathering" of existing participants. This favors
  predictability ("your subscriptions are exactly what you get") over
  guaranteeing continuity on tickets already in flight. If that proves too
  strict in practice, the extension point is
  `list_fanout_destinations`: add a `UNION` with systems that already hold
  a `conversation_participants` row, the same way Decision 3's original
  model worked.
- **No new loop risk relative to Decision 5**: the source is still
  unconditionally excluded from its own fan-out, and the destination query
  still runs exactly once per inbound event — subscriptions only widen the
  candidate list evaluated once, they don't add a new recursive trigger
  path.

**Payload shape**: since fan-out can now reach a system with no prior link
to the conversation, `OutboundTicketEvent` (see Decision 4) has an `event`
field — `"ticket.created"` when the destination has no
`conversation_participants` row yet, `"ticket.updated"` otherwise — so a
destination can tell "please open a ticket" apart from "update your
existing ticket X" (via `external_ref`, present only on `ticket.updated`).
This was originally implemented as a per-system `on_create`/`on_update`
payload-template mechanism; Decision 4 replaced that with one fixed shape
for every destination, `event` included.

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
