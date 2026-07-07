# Examples: patient insurance verification on the `PATIENT_ADMIN` topic

This is the flagship use case for Ticket Bridge: two systems having almost
a chat, via a shared `conversation_id`, about a single real-world case -
`system_a` (Clinical Team ServiceDesk) flags that a patient has no
insurance on file; `system_b` (Patient Registration & Insurance) picks it
up, works it, and reports back; `system_a` sees every update land
automatically because it's subscribed to the same topic (`PATIENT_ADMIN`,
seeded in `migrations/002_seed_example.sql`).

**A note on patient data**: the bridge enforces a fixed *shape* (README.md
"Integration contract", CLAUDE.md Decision 4) - `conversation_id`,
`status`, the two systems' own ticket refs, and a `metadata` field for
whatever structured result the two teams agree to exchange (here, the
confirmed insurance number - and, realistically, a short human-written
note, since there's usually a person typing something at their end of the
exchange too, not just a status transition). It does not enforce or
interpret what goes *inside* `metadata`. The example below uses a case
number (`Patient #4471`) rather than a real name, and puts only the
confirmed insurance number and a short note - not the patient's name,
DOB, or other clinical detail - in `metadata`, as a sensible default for
what's safe to pass through a shared correlation system versus what
should stay looked up locally via the ticket ref.

**Where `note`/`insurance_number` come from**: nothing in the bridge
defines these key names - they're documented on the `PATIENT_ADMIN`
topic's own `description` field (seeded in `002_seed_example.sql`, also
visible via `GET /api/v1/topics/PATIENT_ADMIN` or the "Topics" tab), which
is this project's convention for recording a topic's expected `metadata`
shape. It's documentation, not validation - see README.md "Integration
contract" and CLAUDE.md Decision 4.

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

### 1. `system_a` flags that the patient has no insurance on file

```bash
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{
        "external_ref": "CASE-4471",
        "status": "new",
        "subject": "Patient #4471 - no insurance on file",
        "topic_code": "PATIENT_ADMIN",
        "metadata": {"note": "Patient checked in without an insurance card - please verify coverage."}
      }'
```

`system_a` is itself subscribed to `PATIENT_ADMIN` (required to create a
ticket there - see "Error scenarios" below), so this succeeds and returns
a new `conversation_id` - the shared thread for this case. `metadata.note`
is the free-text comment a person at the front desk typed while opening
the case - just another key in the same generic `metadata` object.

**What the fan-out sends**: `system_b` is subscribed to `PATIENT_ADMIN`
and has no case linked to this conversation yet, so it gets `"event":
"ticket.created"` with `external_ref: null` - it has nothing of its own to
reference yet:

```json
{
  "event": "ticket.created",
  "conversation_id": "<conversation_id>",
  "status": "new",
  "source_system": "system_a",
  "source_ref": "CASE-4471",
  "external_ref": null,
  "conversation_subject": "Patient #4471 - no insurance on file",
  "metadata": {"note": "Patient checked in without an insurance card - please verify coverage."}
}
```

### 2. `system_b` picks it up, validates the patient, and reports "under way"

In a real integration, `system_b`'s own adapter would open its case and
report back automatically upon receiving the payload above - opening the
case and reporting progress in a single call, since it already knows it's
being worked on:

```bash
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-b" \
  -d '{
        "conversation_id": "<conversation_id from step 1>",
        "external_ref": "INSVER-8842",
        "status": "in_progress",
        "metadata": {"note": "On it - contacting the insurer now to confirm coverage."}
      }'
```

Note `topic_code` is omitted - it's optional (and immutable) once the
conversation exists.

**What the fan-out sends**: fan-out excludes only the *source* of the
event, not other already-linked participants - so this notifies
`system_a`, which already has `CASE-4471` linked, with `"event":
"ticket.updated"` - this is the update `system_a` "receives" in the chat,
note included:

```json
{
  "event": "ticket.updated",
  "conversation_id": "<conversation_id>",
  "status": "in_progress",
  "source_system": "system_b",
  "source_ref": "INSVER-8842",
  "external_ref": "CASE-4471",
  "conversation_subject": "Patient #4471 - no insurance on file",
  "metadata": {"note": "On it - contacting the insurer now to confirm coverage."}
}
```

### 3. `system_b` finds the insurance number and resolves its case

This is the actual goal of the whole exchange: getting the confirmed
insurance number back to `system_a`. It travels in `metadata`, attached to
the same `resolved` update - alongside the note the insurance clerk
actually typed, because there's always a person writing something, not
just a status transition:

```bash
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-b" \
  -d '{
        "conversation_id": "<conversation_id>",
        "external_ref": "INSVER-8842",
        "status": "resolved",
        "metadata": {
          "insurance_number": "INS-2298104",
          "note": "The insurance number is now configured."
        }
      }'
```

**What the fan-out sends**: `system_a` gets another `"event":
"ticket.updated"`, same fixed shape - but this time `metadata` carries
both the structured result of `system_b`'s work and the human note that
came with it. `system_a`'s adapter reads `metadata.insurance_number`
directly off this payload (and can show `metadata.note` to whoever's
looking at the case); it doesn't need a separate out-of-band call back
into `system_b`'s API just to learn the one piece of data this whole case
was about:

```json
{
  "event": "ticket.updated",
  "conversation_id": "<conversation_id>",
  "status": "resolved",
  "source_system": "system_b",
  "source_ref": "INSVER-8842",
  "external_ref": "CASE-4471",
  "conversation_subject": "Patient #4471 - no insurance on file",
  "metadata": {
    "insurance_number": "INS-2298104",
    "note": "The insurance number is now configured."
  }
}
```

### 4. `system_a` confirms and closes the case - closing the loop

```bash
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{
        "conversation_id": "<conversation_id>",
        "external_ref": "CASE-4471",
        "status": "closed",
        "metadata": {"note": "Confirmed, patient record updated. Closing the case - thanks!"}
      }'
```

**What the fan-out sends**: `system_b` gets `"event": "ticket.updated"`
with `status: "closed"`, referencing `INSVER-8842`, and the closing note -
both sides now agree the case is done:

```json
{
  "event": "ticket.updated",
  "conversation_id": "<conversation_id>",
  "status": "closed",
  "source_system": "system_a",
  "source_ref": "CASE-4471",
  "external_ref": "INSVER-8842",
  "conversation_subject": "Patient #4471 - no insurance on file",
  "metadata": {"note": "Confirmed, patient record updated. Closing the case - thanks!"}
}
```

Notice the *shape* never changed across all four messages, in either
direction - `metadata`'s contents differed every time (a note alone, a
note plus a structured result, a closing note), but the field itself was
always there. That's the entire point of the fixed integration contract
(README.md "Integration contract", CLAUDE.md Decision 4): both teams
build their adapter once, against one contract, regardless of which
system originates a given update or what business data and human
commentary, if any, ride along with it.

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
  -d '{"conversation_id": "<conversation_id>", "external_ref": "CASE-4471", "status": "new", "topic_code": "SPM"}'
# -> 409: "Conversation ... belongs to topic 'PATIENT_ADMIN', not 'SPM' - topics are immutable after creation."
```

**Sending a non-canonical status** (only `new`, `in_progress`,
`waiting_third_party`, `resolved`, `closed` are accepted - see README.md
"Integration contract"):

```bash
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{"external_ref": "X", "status": "Open", "topic_code": "PATIENT_ADMIN"}'
# -> 422: status must be one of 'new', 'in_progress', 'waiting_third_party', 'resolved', 'closed'
```
