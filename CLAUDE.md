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

## Decision 9 — One generic outbound authentication mechanism, not a choice of types

**Superseded approach**: `systems.auth_type` was an enum (`api_key` |
`bearer` | `basic`), each branch hardcoding how `dispatcher.deliver()`
applied the resolved secret: `api_key` put it in a configurable header
(default `X-API-Key`); `bearer` always used `Authorization: Bearer
<secret>`; `basic` always used `Authorization: Basic <secret>`.

**Why it was reversed**: answering a support question about how to
configure Basic Auth surfaced that it never actually worked ergonomically
- HTTP Basic Auth requires `Authorization: Basic base64("user:password")`,
but the code did `f"Basic {resolved_secret}"` with no encoding step, so
`secret_ref` had to resolve to an already-base64-encoded credential pair,
pre-computed by hand, with nothing in the code, UI, or docs explaining
that. Looking at the other two options while fixing this, `api_key` and
`bearer` turned out to be doing the exact same thing - put a secret in a
header - differing only in *which* header and whether there's a fixed
string prefix on the value. Three enum branches for what's really one
mechanism with two knobs was accidental complexity, not a real design
requirement.

**Decision made**: `systems.auth_type` is gone (dropped by migration
`004_unify_auth_mechanism.sql`). There is one mechanism: if a secret is
configured, it's placed into `auth_config['header']` (default
`X-API-Key`), optionally prefixed with `auth_config['value_prefix']`
(e.g. `"Bearer "`, with a trailing space, reproduces the old `bearer`
behavior exactly; empty by default reproduces the old `api_key`
behavior exactly). Basic Auth is dropped entirely rather than fixed -
nothing in this project needs it today, and if it's needed later the
right fix is a real `base64(username:password)` encoding step in
`dispatcher.py`, not resurrecting a three-way enum.

**What's still out of scope**: this only covers "secret in a header"
schemes. A system requiring OAuth2 token exchange (fetch a short-lived
token, then use it) or HMAC request signing would need real new code in
`dispatcher.py`, not a config change - no worse off than before this
decision, since none of the three original options covered those either.

## Decision 10 — Inbound API key lifecycle: an endpoint + a one-time reveal, not manual SQL

**Problem closed**: `api_keys` (the table backing `X-API-Key` on `POST
/api/v1/events`, see `app/security.py`) has always stored `active` and
`revoked_at` alongside `key_hash`, but until now nothing in the API or
frontend actually used them — a new inbound key could only be issued by
generating one by hand and inserting its hash directly via SQL (still
documented as the fallback in `migrations/002_seed_example.sql`'s
comment). That's a reasonable stopgap for a skeleton's seed data, not a
sustainable way to onboard a real system or to revoke a leaked key
without shell/DB access.

**Decision made**: `POST /api/v1/systems/{code}/api-keys` generates a key
server-side (`secrets.token_urlsafe(32)`, via
`app/security.py:generate_api_key`), persists only its SHA-256 hash, and
returns the plaintext exactly once, in that response. `GET
.../api-keys` lists a system's keys by metadata only (`description`,
`active`, `created_at`, `revoked_at` — never plaintext or hash); `DELETE
.../api-keys/{key_id}` sets `active = FALSE, revoked_at = now()` rather
than hard-deleting, so a revoked key still shows up in the audit trail
instead of silently disappearing. All three write to `audit_log`
(`inbound_api_key_created` / `inbound_api_key_revoked`), the same
convention `systems.py`'s existing CRUD already follows.

The frontend mirrors this exactly: the "Systems" tab's edit dialog (only
once a system has been saved and has a `code` to attach keys to) gets an
"Inbound API keys" section with the same list/generate/revoke, and
generating a key opens a one-time reveal dialog — plaintext shown once in
a read-only field with a copy button and an explicit "won't be shown
again" warning, then the field is cleared on close so the value doesn't
linger in the DOM. This is the same UX Kibana uses for Elasticsearch API
keys and Stripe/GitHub use for theirs — not a novel pattern, chosen
because it's the shape users already expect for a secret that is
mechanically incapable of being displayed again (only the hash exists
server-side after creation).

**Why generation is server-side, not typed in by an admin**: unlike
`systems.auth_config.secret_ref` (Decision 9), which names a secret that
already exists somewhere else (Secret Manager, `.env`), an inbound key
has no other source of truth to point at — the bridge itself is the only
party that needs to mint it. Letting an admin type in an arbitrary string
would reintroduce exactly the weak/reused/guessable-secret problem
`secrets.token_urlsafe` exists to avoid.

**Consciously not done**: no endpoint to *list keys across all systems*
(only per-`code`, matching how `auth_config` and topic subscriptions are
already scoped in this file) - there is no dedicated top-level "API Keys"
page, since every key belongs to exactly one system and the existing
Systems tab is already the natural place an admin would look for it. If
key volume per system ever grows large enough that this list becomes
unwieldy, the extension point is `list_api_keys` in `app/api/systems.py`.

## Decision 11 — Admin frontend authentication: bcrypt + DB-backed sessions, not JWT/IAP

**Problem closed**: this project's original design (see the now-removed
"Human authentication for the frontend" item below) deliberately deferred
human auth for the Systems/Topics/Conversations/Audit frontend to an
external layer - specifically Cloud Run IAP, or an authenticated tunnel.
That assumption held only as long as the deployment target was Cloud Run.
It has since moved to a self-hosted VM/container behind a plain nginx
reverse proxy (see the reverse-proxy-path-prefix work earlier in this
file's history), which provides no such layer - the admin frontend had,
in practice, zero authentication of its own.

**Decision made**: real in-app authentication, added by
`migrations/005_admin_authentication.sql` and `app/api/auth.py`:

- **Password hashing: bcrypt**, not a hand-rolled stdlib scheme. Every
  other credential in this project (`api_keys.key_hash`,
  `sessions.token_hash`) is a fast SHA-256 hash of a high-entropy
  server-generated secret, which is correct for those - they're never
  brute-forceable dictionary targets. A human-chosen password is exactly
  that kind of target, so it needs a deliberately slow, salted algorithm;
  bcrypt is the well-audited standard for this, and the one new dependency
  it costs is a reasonable trade against hand-rolling PBKDF2 iteration
  counts and constant-time comparisons correctly.
- **Sessions are DB-backed** (`sessions` table), not a stateless JWT.
  Chosen specifically so logout/revocation is immediate and inspectable
  via plain SQL - the same reasoning `api_keys` already uses
  (`active`/`revoked_at`, never a hard delete). A stateless JWT would need
  a separate revocation blocklist to get the same property, which is more
  moving parts for no benefit at this project's scale (Decision 6: no ORM,
  plain SQL, keep it simple).
- **Transport is an httpOnly cookie**, not a bearer token the frontend
  holds in JS/localStorage. This was the deciding factor for not touching
  every existing `fetch()` call site in `app.js`: the browser attaches the
  cookie automatically, so `loadSystems`/`loadTopics`/etc. needed zero
  changes. A single `window.fetch` wrapper at the top of `app.js`
  redirects to `login.html` on any 401, so an expired/revoked session
  mid-use is handled the same way as on page load (`start()`'s `GET
  /api/v1/auth/me`) - consequently no admin endpoint may use 401 for
  anything but "no valid session" (a wrong *current* password on
  `change-password` is a 400 for this reason). `SameSite=Lax` plus this being a
  same-origin, low-traffic internal admin tool is judged sufficient CSRF
  mitigation without adding a separate CSRF-token mechanism.
- **Default admin seeded in the migration**, not bootstrapped from env
  vars at startup. Simpler and always present after running migrations,
  at the cost of a known-shape default credential (`admin` /
  `ChangeMe-Immediately!`, see README.md section 5) sitting in git
  history - the same trade-off `002_seed_example.sql` already makes for
  its dev API keys, except this one seeds in every environment (there is
  no other bootstrap path into `users`), so the loud "change this
  immediately" documentation matters more here than it does for dev-only
  seed data.
- **Login brute-force throttling is per-username, counted from
  `audit_log`**, not a separate table or an in-memory counter. Failed
  logins are recorded as `admin_login_failed` (worth auditing anyway);
  `_recent_failed_logins` in `app/api/auth.py` counts them within
  `LOGIN_LOCKOUT_MINUTES`, ignoring any before the username's last
  successful login, and at `LOGIN_MAX_FAILED_ATTEMPTS` further attempts
  get a 429 before bcrypt runs. Being DB-backed, it holds across multiple
  instances, which an in-process counter wouldn't. Per-username rather
  than per-IP because the known default username is the real target, and
  a per-IP limit behind nginx would need trusted `X-Forwarded-For`
  handling. Accepted cost: anyone can keep a known username locked out by
  continuing to fail against it - an availability nuisance, not a breach,
  handled by blocking the source at the proxy.
- **Changing a password revokes the user's other sessions**, not the one
  making the change - a leaked or forgotten session must not outlive the
  password it was opened with.

**Scope, deliberately**: this protects the human admin surface only -
`systems`/`topics`/`conversations`/`audit` routers, plus a client-side
redirect for the static frontend. `POST /api/v1/events` (per-system API
key) and `POST /api/v1/sync` (scheduler shared secret) are untouched -
those already authenticate distinct, non-human callers correctly; folding
them into admin sessions would conflate two different trust boundaries.

**Consciously not done**: no password-change *policy* beyond a minimum
length (8 chars, `ChangePasswordRequest` in `schemas.py`) - no complexity
rules, no expiry, no MFA. No self-service account creation - `users` rows
are DB-only for now, same maturity level `api_keys` was at before
Decision 10 (a natural next extension point if more than one human admin
is ever needed). No pruning of expired/revoked `sessions` rows - mirrors
`api_keys`, which also never prunes; fine at this data volume.

## Decision 12 — The simulator is an independent sub-project, not part of the app

**Problem closed**: testing a conversation end to end needs at least two
systems, and a dev environment often has only one real one.
`examples/dummy_system.sh` covers that from a terminal; `simulator/` is
the same idea with a UI - a stand-in external system that sends, receives
and replies to tickets.

**Decision made**: it lives in this repo (`simulator/`) but shares nothing
with `app/` at the code level - its own `pyproject.toml`/`uv.lock`,
Dockerfile, CI job, and its own copy of the integration contract
(`simulator/sim/contract.py`), talking to the bridge only over the
public HTTP API with a per-system API key. That's deliberate: it should
behave exactly like another team's adapter, including breaking the same
way if the contract changes - importing `app.schemas` would hide exactly
the drift it exists to catch. Same repo rather than a separate one so it
versions and runs (compose profile `simulator`) alongside the bridge it
targets; being self-contained, it can move to its own repo as-is if other
teams ever want it as a reference adapter.

**Consciously not done**: no login on the simulator UI (it's a dev tool -
compose binds it to 127.0.0.1 only); no published image (built from
`./simulator` by compose); no bridge admin calls - registering it as a
system stays a manual step in the bridge's Systems tab, so the simulator
never needs admin credentials.

## What this skeleton assumes and leaves undecided

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
