# Examples: `system_a`/`system_b` on the `INFRA` topic

A worked walkthrough of the seed data from `migrations/002_seed_example.sql`:
`system_a` (Clinical Team ServiceDesk) is subscribed to `INFRA`; `system_b`
(Infrastructure Team ITSM) is subscribed to `INFRA` and `SPM`. This shows
what happens, step by step, when a ticket moves through both systems -
including the exact JSON payload the fan-out mechanism builds at each
step. Notice it's the **same shape every time**, for both systems - that's
the whole point of the fixed integration contract (see README.md
"Integration contract" and CLAUDE.md Decision 4): there's no per-system
customization to configure or reason about.

Two ways to run it:

- **`./walkthrough.sh`** - runs the full scenario against your local
  instance using the seed data as-is. Since the seed's `base_url`s
  (`https://system-a.example.local/...`, `https://system-b.example.local/...`)
  are fictional, `/api/v1/sync` calls will report delivery failures - the
  script explains this at each step and prints the payload that *would
  have* been sent, matching what the code actually builds.
- **`./live_delivery_demo.sh`** - a better way to see it: temporarily
  points `system_b` at a tiny local mock server (`mock_receiver.py`, stdlib
  only, no new dependencies) so you watch a **real** HTTP delivery happen
  and print the exact payload bytes Ticket Bridge sent, then restores
  `system_b`'s original `base_url` automatically when the script exits.

Both scripts assume the app is running locally (`uv run uvicorn
app.main:app --reload --port 8080`, see README.md section 3) with
`migrations/001_initial_schema.sql`, `002_seed_example.sql`, and
`003_standardize_ticket_status.sql` applied, and read
`SCHEDULER_SHARED_SECRET` from `../.env` if present.

```bash
chmod +x examples/*.sh   # if not already executable
./examples/walkthrough.sh
# or
./examples/live_delivery_demo.sh
```

---

## The scenario, step by step

### 1. `system_a` creates a new ticket under `INFRA`

```bash
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{
        "external_ref": "TICKET-1001",
        "status": "new",
        "subject": "Print queue stuck on floor 3",
        "topic_code": "INFRA"
      }'
```

`system_a` is itself subscribed to `INFRA` (required to create a ticket
there - see "Error scenarios" below), so this succeeds and returns a new
`conversation_id`.

**What the fan-out sends**: `system_b` is subscribed to `INFRA` and has no
ticket linked to this conversation yet, so it gets `"event":
"ticket.created"` with `external_ref: null` - it has nothing of its own to
reference yet:

```json
{
  "event": "ticket.created",
  "conversation_id": "<conversation_id>",
  "status": "new",
  "source_system": "system_a",
  "source_ref": "TICKET-1001",
  "external_ref": null,
  "conversation_subject": "Print queue stuck on floor 3"
}
```

### 2. Sync runs, `system_b` receives the payload and opens its own ticket

In a real integration, `system_b`'s own adapter would open a ticket and
report back automatically upon receiving the payload above. Here, that's
the next manual call:

```bash
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-b" \
  -d '{
        "conversation_id": "<conversation_id from step 1>",
        "external_ref": "INC-2001",
        "status": "new"
      }'
```

Note `topic_code` is omitted - it's optional (and immutable) once the
conversation exists.

**What the fan-out sends**: fan-out excludes only the *source* of the
event, not other already-linked participants - so this also notifies
`system_a`, which already has `TICKET-1001` linked, with `"event":
"ticket.updated"` referencing that ref:

```json
{
  "event": "ticket.updated",
  "conversation_id": "<conversation_id>",
  "status": "new",
  "source_system": "system_b",
  "source_ref": "INC-2001",
  "external_ref": "TICKET-1001",
  "conversation_subject": "Print queue stuck on floor 3"
}
```

### 3. `system_a` updates the ticket to `in_progress`

```bash
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{
        "conversation_id": "<conversation_id>",
        "external_ref": "TICKET-1001",
        "status": "in_progress"
      }'
```

**What the fan-out sends**: `system_b` now *has* a ticket linked
(`INC-2001`, from step 2), so this time it gets `"event": "ticket.updated"`
referencing its own known ticket - same shape as step 2, just addressed
to the other system:

```json
{
  "event": "ticket.updated",
  "conversation_id": "<conversation_id>",
  "status": "in_progress",
  "source_system": "system_a",
  "source_ref": "TICKET-1001",
  "external_ref": "INC-2001",
  "conversation_subject": "Print queue stuck on floor 3"
}
```

This is the core behavior: the *same* conversation produces a
`ticket.created` payload the first time a destination sees it, and
`ticket.updated` every time after, purely based on whether
`conversation_participants` already has a row for that destination - see
`CLAUDE.md` Decision 8. The shape itself never changes between systems -
only the values do (see Decision 4).

### 4. `system_b` resolves its ticket - fan-out flows the other way

```bash
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-b" \
  -d '{
        "conversation_id": "<conversation_id>",
        "external_ref": "INC-2001",
        "status": "resolved"
      }'
```

**What the fan-out sends**: `system_a` already has `TICKET-1001` linked,
so it gets `"event": "ticket.updated"` too - the canonical `status` value
(`resolved`) is sent as-is; translating it to whatever `system_a`'s own
internal vocabulary calls "resolved" is `system_a`'s own adapter's job,
not the bridge's:

```json
{
  "event": "ticket.updated",
  "conversation_id": "<conversation_id>",
  "status": "resolved",
  "source_system": "system_b",
  "source_ref": "INC-2001",
  "external_ref": "TICKET-1001",
  "conversation_subject": "Print queue stuck on floor 3"
}
```

### 5. Trigger sync and inspect the result

```bash
curl -X POST http://localhost:8080/api/v1/sync \
  -H "X-Scheduler-Secret: <your SCHEDULER_SHARED_SECRET>"

curl http://localhost:8080/api/v1/conversations/<conversation_id>
curl "http://localhost:8080/api/v1/audit?conversation_id=<conversation_id>"
```

If you have direct database access, the raw enqueued payloads (ground
truth, not just this document's description of them) are visible via:

```bash
psql "$DATABASE_URL" -c "SELECT id, destination, source, status, payload FROM outbox ORDER BY id;"
```

---

## Error scenarios worth knowing

**Creating a ticket under a topic you don't subscribe to** (`system_a` is
not subscribed to `SALES`):

```bash
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{"external_ref": "X", "status": "new", "topic_code": "SALES"}'
# -> 403: "System 'system_a' must be subscribed to topic 'SALES' to create a ticket in it."
```

**Sending a mismatched `topic_code` on an existing conversation** (topics
are immutable after creation):

```bash
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{"conversation_id": "<conversation_id>", "external_ref": "TICKET-1001", "status": "new", "topic_code": "SPM"}'
# -> 409: "Conversation ... belongs to topic 'INFRA', not 'SPM' - topics are immutable after creation."
```

**Sending a non-canonical status** (only `new`, `in_progress`,
`waiting_third_party`, `resolved`, `closed` are accepted - see README.md
"Integration contract"):

```bash
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{"external_ref": "X", "status": "Open", "topic_code": "INFRA"}'
# -> 422: status must be one of 'new', 'in_progress', 'waiting_third_party', 'resolved', 'closed'
```
