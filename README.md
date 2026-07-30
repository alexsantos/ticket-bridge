# Ticket Bridge

[![Tests](https://github.com/alexsantos/ticket-bridge/actions/workflows/tests.yml/badge.svg)](https://github.com/alexsantos/ticket-bridge/actions/workflows/tests.yml)
[![Docker image](https://github.com/alexsantos/ticket-bridge/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/alexsantos/ticket-bridge/pkgs/container/ticket-bridge)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Lightweight ticket correlation service across multiple support applications,
designed to replace the "central hub" role that OSTicket used to play
implicitly when two (or more) teams used the same tool to track processes
between each other.

Each team keeps using its own operational support application. Ticket
Bridge sits in the middle: it receives creation/update events from one
system, maintains the correlation (`conversation_id`), and distributes
("fans out") the status change to every system currently subscribed to
that ticket's **topic** (a mandatory category like `INFRA`, `SPM`, or
`SALES` — see section 1).

### A concrete example

Two systems, one shared thread: a clinical system (`system_a`) flags that
a patient has no insurance on file; a patient registration/insurance
system (`system_b`) picks it up, verifies coverage, and reports back the
confirmed insurance number - almost like a chat, via the shared
`conversation_id`:

```
system_a → "Patient #4471 has no insurance on file"        (new)
system_b → "On it - contacting the insurer now."           (in_progress)
system_b → "The insurance number is now configured."       (resolved, metadata.insurance_number: "INS-2298104")
system_a → "Confirmed, patient record updated. Closing."   (closed)
```

Every message, on every topic, uses the exact same fixed shape (see
"Integration contract" below) - only the values, and whatever's in
`metadata`, differ. See [`examples/README.md`](examples/README.md) for
the full runnable walkthrough of this scenario, including a live-delivery
demo that lets you watch a real payload arrive over HTTP.

See **`CLAUDE.md`** for the full rationale behind the architecture decisions.

---

## Integration contract

Every system that integrates with Ticket Bridge speaks the same fixed
contract - there is no per-system status mapping or payload template to
configure (see CLAUDE.md Decision 4). If your team is building the
adapter that connects your support tool to the bridge, this section is
everything you need.

### Canonical status vocabulary

Every `status` value, in both directions (what you send to the bridge and
what you receive from it), must be exactly one of these five:

| Status | Meaning |
|---|---|
| `new` | Ticket just created, not yet worked on |
| `in_progress` | Actively being worked on |
| `waiting_third_party` | Blocked on someone outside the two systems |
| `resolved` | Fix applied, pending confirmation/closure |
| `closed` | Done |

Your own system's internal statuses (`Open`, `NEW`, `In Progress`, etc.)
are **your** adapter's responsibility to translate to/from this list. The
bridge enforces this at the API boundary (`POST /api/v1/events` rejects
anything else with `422`) and at the database level (a `CHECK` constraint
on the stored status columns) - there is nothing to configure on the
bridge side.

### Outbound payload shape

When your system is subscribed to a topic (see section 1) and a ticket in
it is created or updated, your `base_url` receives exactly this shape -
identical for every system, always:

```json
{
  "event": "ticket.created",
  "conversation_id": "8a1ca7f4-0c8a-4e4d-843f-05c0ab201f07",
  "status": "new",
  "source_system": "system_a",
  "source_ref": "CASE-4471",
  "external_ref": null,
  "conversation_subject": "Patient #4471 - no insurance on file",
  "metadata": {"note": "Patient checked in without an insurance card - please verify coverage."}
}
```

- `event`: `"ticket.created"` the first time your system sees this
  conversation (you have no ticket of your own yet, so `external_ref` is
  `null`); `"ticket.updated"` every time after (referencing the ticket ref
  you yourself reported back the first time).
- `source_system` / `source_ref`: which system and ticket ID originated
  this event - useful for cross-referencing even before you've linked
  your own ticket.
- `external_ref`: **your own** ticket ID for this conversation, once
  known - report it via `POST /api/v1/events` (below) so future updates
  include it.
- `metadata`: whatever structured data *you* attached to *your own* update
  via `POST /api/v1/events` (below) - forwarded as-is, per event, not
  accumulated across the conversation. This is where actual business data
  travels (e.g. a confirmed insurance number, a resolution note) - the
  bridge only enforces the shape around it (`status`, the IDs), never the
  contents of `metadata` itself. Two systems on the same topic need to
  agree between themselves on what keys they expect to find here - **check
  that topic's `description`** (`GET /api/v1/topics/{code}`, or the
  "Topics" tab) first; that's the documented (not enforced) contract for
  `metadata` on that topic. See CLAUDE.md Decision 4.

### Reporting your own ticket / status changes

`POST /api/v1/events`, authenticated with your system's API key
(`X-API-Key` header):

```json
{
  "conversation_id": "8a1ca7f4-0c8a-4e4d-843f-05c0ab201f07",
  "external_ref": "INSVER-8842",
  "status": "resolved",
  "metadata": {
    "insurance_number": "INS-2298104",
    "note": "The insurance number is now configured."
  }
}
```

This is `system_b` reporting the third message from "A concrete example"
above - `conversation_id` is the thread `system_a` started; `external_ref`
is `system_b`'s own case ref, reported back the first time it linked
itself to this conversation. `conversation_id` is omitted only when
creating a brand-new ticket (in which case `topic_code` is required, and
your system must be subscribed to it - see `examples/README.md` step 1
for that call). `subject` and `metadata` are optional on every call -
`subject` is only meaningful at creation, `metadata` travels with
whichever specific update it's attached to. See
[`examples/README.md`](examples/README.md) for the full worked walkthrough
(two systems, both directions, plus a live-delivery demo you can run
locally).

---

## 1. Architecture overview

```
System A ──POST /api/v1/events──▶ ┌──────────────────────┐
                                    │   Ticket Bridge       │
System B ──POST /api/v1/events──▶ │  (FastAPI, any host    │
                                    │   that runs it         │
System C ──POST /api/v1/events──▶ │   continuously)          │
                                    │                           │
            ◀── outbox (HTTP) ──── │  PostgreSQL:               │
                                    │   - conversations           │
                                    │   - participants              │
                                    │   - topics + subscriptions      │
                                    │   - outbox (queue)                │
                                    │   - audit_log                       │
                                    └──────────┬───────────┘
                                               │
                                     in-process scheduler
                                     (app/scheduler.py, triggers
                                      sync every 1-2 min by default -
                                      see section 3.4)
```

- **No external broker** (RabbitMQ/Pub-Sub): the queue is a Postgres table
  (`outbox`), processed with `SELECT ... FOR UPDATE SKIP LOCKED`.
- **Stateless**: any instance can process any request; all state lives in
  the database.
- **No external pinger required**: an in-process scheduler
  (`app/scheduler.py`) triggers outbox processing automatically on a fixed
  interval - see section 3.4. `POST /api/v1/sync` still exists as a manual
  trigger, and as the integration point for an external scheduler (e.g.
  Cloud Scheduler) if this ever runs on something that scales to zero.
- **N systems, not just 2**: `conversation_participants` allows associating
  as many systems as needed with the same conversation.
- **Topic-driven, proactive fan-out**: every conversation belongs to exactly
  one `topic` (e.g. `INFRA`); a system receives a ticket and every one of
  its updates as long as it's subscribed to that topic — including brand
  new tickets it has never seen before, not just ones it already linked to.
  See CLAUDE.md Decision 8.

---

## 2. Project structure

```
ticket-bridge/
├── app/
│   ├── main.py                  # app startup, routers, health check
│   ├── config.py                # configuration via environment variables
│   ├── database.py              # Postgres connection pool (psycopg3)
│   ├── scheduler.py              # in-process periodic outbox-sync trigger (APScheduler)
│   ├── models.py                # internal domain models
│   ├── schemas.py                # API request/response contracts
│   ├── security.py              # authentication (API keys, sync trigger secret)
│   ├── api/
│   │   ├── events.py            # POST /api/v1/events   (inbound)
│   │   ├── sync.py              # POST /api/v1/sync     (manual/on-demand outbox trigger)
│   │   ├── systems.py           # CRUD /api/v1/systems  (configuration + topic subscriptions)
│   │   ├── topics.py            # CRUD /api/v1/topics   (ticket categories, e.g. INFRA/SPM/SALES)
│   │   ├── conversations.py     # GET  /api/v1/conversations
│   │   └── audit.py             # GET  /api/v1/audit
│   ├── services/
│   │   ├── correlation_service.py  # conversation/participant management + fan-out resolution
│   │   ├── payload_builder.py      # builds the one fixed outbound payload shape
│   │   ├── outbox_service.py       # table-based transactional queue (outbox pattern)
│   │   ├── sync_service.py         # outbox batch processing (called by scheduler.py and api/sync.py)
│   │   ├── dispatcher.py           # HTTP delivery to each system
│   │   ├── secrets.py              # secret resolution (Secret Manager / env)
│   │   └── audit_service.py        # audit_log read/write
│   └── frontend/
│       ├── index.html           # configuration/audit panel
│       ├── style.css
│       └── app.js
├── migrations/
│   ├── 001_initial_schema.sql              # full schema, incl. topics/subscriptions (run first)
│   ├── 002_seed_example.sql                # sample systems, topics, subscriptions (development only)
│   ├── 003_standardize_ticket_status.sql   # drops per-system status_mapping/payload_template, adds CHECK constraints
│   └── 004_unify_auth_mechanism.sql        # drops auth_type - one generic header-based auth mechanism
├── tests/
│   └── test_payload_builder.py
├── examples/
│   ├── README.md                # flagship walkthrough: patient insurance verification "chat"
│   ├── walkthrough.sh           # runs the full scenario locally
│   ├── live_delivery_demo.sh    # same, but with a real HTTP delivery you can watch
│   └── mock_receiver.py         # tiny local webhook stand-in used by live_delivery_demo.sh
├── pyproject.toml               # project metadata and dependencies (uv)
├── uv.lock                      # pinned, reproducible dependency versions
├── .python-version              # Python version pinned for uv
├── Dockerfile
├── .env.example
├── CLAUDE.md                    # architecture rationale
└── README.md                    # this file
```

Each `.py` file has a docstring at the top explaining its responsibility —
start there when exploring the code in PyCharm.

---

## 3. Running locally (development)

### 3.1. Prerequisites
- [uv](https://github.com/astral-sh/uv) (manages the Python version, the
  virtual environment, and dependencies - no separate Python install needed)
- Local PostgreSQL 15+ (or via Docker)
- PyCharm (open the `ticket-bridge/` folder as a project; point the
  interpreter at `.venv` created by `uv sync`, and mark `app` as "Sources
  Root" if needed)

### 3.2. Steps

```bash
# 1. Install uv, if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install dependencies (uv creates .venv and installs the pinned
#    versions from uv.lock automatically)
uv sync

# 3. Create the local database
createdb ticketbridge

# 4. Run the migrations, in order
psql "postgresql://localhost/ticketbridge" -f migrations/001_initial_schema.sql
psql "postgresql://localhost/ticketbridge" -f migrations/002_seed_example.sql   # optional, sample data
psql "postgresql://localhost/ticketbridge" -f migrations/003_standardize_ticket_status.sql
psql "postgresql://localhost/ticketbridge" -f migrations/004_unify_auth_mechanism.sql

# 5. Configure environment variables
cp .env.example .env
# edit .env with your local DB credentials

# 6. Start the server
uv run uvicorn app.main:app --reload --port 8080
```

Access:
- **Frontend**: http://localhost:8080/
- **Interactive API documentation (Swagger)**: http://localhost:8080/docs
- **Health check**: http://localhost:8080/health

### 3.3. Testing the end-to-end flow locally

The seed data (`002_seed_example.sql`) subscribes `system_a` to `INFRA`,
and `system_b` to both `INFRA` and `SPM`. Creating an `INFRA` ticket as
`system_a` therefore fans out immediately to `system_b`, since it's
subscribed - no prior linking required:

```bash
# Create a ticket from "system_a" under the INFRA topic (uses the seed's sample key)
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{"external_ref": "TICKET-123", "status": "new", "subject": "Lab printer malfunction", "topic_code": "INFRA"}'

# Because "system_b" is subscribed to INFRA, this immediately creates an
# outbox row destined for it - check via:
psql "postgresql://localhost/ticketbridge" -c "SELECT id, destination, status FROM outbox;"

# A system may only create tickets under topics it is itself subscribed
# to - e.g. creating under "SALES" as "system_a" (not subscribed) returns 403.
```

For a full walkthrough of this scenario in both directions (including a
live-delivery demo that lets you watch the real fan-out payload arrive
over HTTP instead of just reading about it), see
[`examples/README.md`](examples/README.md).

To run the automated tests:
```bash
uv run pytest tests/ -v
```

### 3.4. Automatic background sync

By default, the app starts an in-process scheduler (`app/scheduler.py`,
built on [APScheduler](https://apscheduler.readthedocs.io/)) that calls
the same outbox-processing logic as `POST /api/v1/sync` every
`SYNC_INTERVAL_SECONDS` (120s by default) - no external pinger like Google
Cloud Scheduler is required. This is why the delivery in the example above
happens automatically within about two minutes even if you never call
`/api/v1/sync` yourself; to see it immediately, either lower
`SYNC_INTERVAL_SECONDS` in `.env` or call the endpoint manually:

```bash
curl -X POST http://localhost:8080/api/v1/sync \
  -H "X-Scheduler-Secret: $(grep SCHEDULER_SHARED_SECRET .env | cut -d= -f2)"
```

Set `SYNC_SCHEDULER_ENABLED=false` to disable the in-process scheduler
entirely and drive sync purely through the endpoint (e.g. if you deploy to
Cloud Run and use Cloud Scheduler instead - see section 4.6).

### 3.5. Running as a plain container (VM, no GCP)

Because sync no longer depends on an external pinger, running this on a
plain VM is just: build the image, run it with a reachable Postgres, and
leave it running - nothing else to wire up.

```bash
docker build -t ticket-bridge .

docker run -d --name ticket-bridge \
    -p 8080:8080 \
    -e DATABASE_URL="postgresql://ticketbridge:PASSWORD@your-postgres-host:5432/ticketbridge" \
    -e SCHEDULER_SHARED_SECRET="$(openssl rand -base64 32)" \
    -e SYSTEM_A_OUTBOUND_KEY="..." \
    ticket-bridge
```

`SYSTEM_A_OUTBOUND_KEY` above is that system's `auth_config.secret_ref`
(`system_a_outbound_key`), UPPERCASED - secret resolution does
`secret_ref.upper()` when reading from the environment (see
`app/services/secrets.py`).

Run the migrations against that same `DATABASE_URL` first (section 3.2,
step 4). `SYNC_SCHEDULER_ENABLED` defaults to `true`, so outbox processing
starts automatically as soon as the container comes up - no Cloud
Scheduler, no cron, nothing external to configure. The rest of this
section (4) is GCP/Cloud Run-specific and can be skipped entirely for this
deployment mode.

> **Secrets outside GCP**: `secrets.py` currently only knows two modes -
> `ENVIRONMENT=local` reads each `secret_ref` from an environment variable
> of the same name (as used above), anything else assumes GCP Secret
> Manager is available. There's no generic "production, but not GCP" mode
> yet, so a non-GCP VM should keep `ENVIRONMENT=local` (despite the name)
> to get env-var-based secrets - or extend `secrets.py` with a real third
> backend if that naming bothers you enough to fix it.

### 3.6. Running with Docker Compose

`docker-compose.yml` is sections 3.2 and 3.5 combined into one command -
Postgres with the migrations (and seed data) applied automatically, plus
the app, using the image `docker-publish.yml` already publishes to GHCR
(no local build):

```bash
cp .env.example .env   # if you don't already have one - same file as section 3.2 step 5
docker login ghcr.io   # only if the package is private - see section 7
docker compose pull
docker compose up -d
```

`db`'s init scripts are the same numbered migrations from section 3.2,
mounted read-only into `/docker-entrypoint-initdb.d` - the official
postgres image runs every `*.sql` file there, in filename order, against
an empty data volume on first startup, so this reproduces section 3.2
step 4 exactly (seed data included). Access is the same as section 3.2
(frontend, Swagger docs, health check on `localhost:8080`).

`db` intentionally publishes no host port - it's reachable only from
`app`, over the compose file's own `ticket-bridge` network, by service
name (`db:5432`). This matters on a host that already runs other Postgres
instances/containers: there's no host port to collide with, and nothing
outside this compose project's network can reach this database directly.

`app` loads its configuration from that same `.env` (`env_file:` in
`docker-compose.yml`) - `SCHEDULER_SHARED_SECRET`, `ROOT_PATH`,
`SYSTEM_A_OUTBOUND_KEY`, `SYSTEM_B_OUTBOUND_TOKEN`, everything - so
there's one file to edit regardless of whether you run this via `uv run
uvicorn` or `docker compose`, not a separate copy hardcoded in the
compose file. The one exception is `DATABASE_URL`: `docker-compose.yml`
overrides it to point at `db` (this stack's own Postgres, reachable only
by that service name on the `ticket-bridge` network) instead of `.env`'s
`localhost`, which would resolve to the `app` container itself. The
`.env.example` defaults (`SCHEDULER_SHARED_SECRET`,
`SYSTEM_A_OUTBOUND_KEY`, `SYSTEM_B_OUTBOUND_TOKEN`) are dev-only
placeholders matching `002_seed_example.sql`'s seeded `secret_ref`
values - replace them in `.env` for anything beyond local/dev use, and
pin `image:` in `docker-compose.yml` to a specific `X.Y.Z` tag instead of
`latest` (section 7.1 cuts those tags).

The scheduler starts automatically (`SYNC_SCHEDULER_ENABLED=true` in
`.env.example`), same as section 3.4.

```bash
docker compose down       # stop, keep the db volume
docker compose down -v    # stop and wipe the db volume too
```

#### 3.6.1. Behind a reverse proxy under a path prefix

`.env`'s `ROOT_PATH` (default `/` - the domain root, i.e. no prefix) is
for running behind a reverse proxy (e.g. HAProxy) that mounts the app
under a path prefix instead, e.g. `/ticket-bridge`: a `path_beg
/ticket-bridge` ACL routing to this container's port 8080, with the
prefix stripped before forwarding (HAProxy: `http-request set-path
%[path,regsub(^/ticket-bridge,)]`), and a redirect adding the trailing
slash when it's missing so relative asset paths resolve correctly (see
below). Set `ROOT_PATH=/ticket-bridge` in `.env` to match.

`ROOT_PATH` is passed to uvicorn's `--root-path` (see `Dockerfile`, which
also bakes in `/` as its own default for plain `docker run` - section
3.5), which fixes ASGI-level path generation - the OpenAPI `servers`
entry, redirects Starlette itself issues - so `/docs` works correctly
under the prefix. That alone isn't sufficient, though:
`app/frontend/index.html` and `app.js` reference their own assets and the
API with paths relative to the current document (`style.css`, `app.js`,
`api/v1/...`, `health` - no leading `/`), specifically so they keep
working unmodified under whatever prefix the proxy strips, without the
app needing to know the prefix to render its own HTML - only the
browser's URL bar needs to end in `/ticket-bridge/` (with the trailing
slash) for that relative resolution to land on the right path.

`.env` is not consumed by local `uv run uvicorn` (section 3.2) - there's
no proxy in front of it locally, so `ROOT_PATH` only matters for the
containerized app (`docker run` - section 3.5 - or `docker compose`).

---

## 4. Deploying to GCP (production)

This section assumes an existing GCP project and an authenticated `gcloud`
(`gcloud auth login`, `gcloud config set project YOUR_PROJECT_ID`).

### 4.1. Enable required APIs

```bash
gcloud services enable \
    run.googleapis.com \
    sqladmin.googleapis.com \
    secretmanager.googleapis.com \
    cloudscheduler.googleapis.com \
    artifactregistry.googleapis.com
```

### 4.2. Database (Cloud SQL - PostgreSQL)

```bash
# Create the instance (adjust the tier to expected load - db-f1-micro
# is more than enough for this traffic volume)
gcloud sql instances create ticket-bridge-db \
    --database-version=POSTGRES_16 \
    --tier=db-f1-micro \
    --region=europe-west1 \
    --storage-auto-increase

# Create the database and user
gcloud sql databases create ticketbridge --instance=ticket-bridge-db
gcloud sql users create ticketbridge \
    --instance=ticket-bridge-db \
    --password="SET_A_STRONG_PASSWORD_HERE"

# Run the migrations via the Cloud SQL Auth Proxy
cloud-sql-proxy YOUR_PROJECT_ID:europe-west1:ticket-bridge-db &
psql "postgresql://ticketbridge:PASSWORD@localhost:5432/ticketbridge" \
    -f migrations/001_initial_schema.sql
psql "postgresql://ticketbridge:PASSWORD@localhost:5432/ticketbridge" \
    -f migrations/003_standardize_ticket_status.sql
psql "postgresql://ticketbridge:PASSWORD@localhost:5432/ticketbridge" \
    -f migrations/004_unify_auth_mechanism.sql
# (002_seed_example.sql is for development only - do not run in production)
```

### 4.3. Secrets (Secret Manager)

Each external system has a secret reference (`secret_ref`) in its
`auth_config` — it's the value the dispatcher uses to authenticate
outbound calls. The Cloud Scheduler shared secret and the DB password
should also live here. See section 4.7 for how `auth_config` as a whole
is configured (there's a single generic mechanism, not a choice of auth
types — see CLAUDE.md Decision 9).

```bash
echo -n "DB_PASSWORD" | gcloud secrets create db-password --data-file=-
echo -n "$(openssl rand -base64 32)" | gcloud secrets create scheduler-shared-secret --data-file=-

# One secret per external system, named exactly like the 'secret_ref'
# configured in the frontend for that system (Secret Manager lookups use
# secret_ref as-is, unlike the local .env fallback - see section 3.5):
echo -n "REPLACE_WITH_REAL_KEY_VALUE" | gcloud secrets create system_a_outbound_key --data-file=-
echo -n "REPLACE_WITH_REAL_TOKEN_VALUE" | gcloud secrets create system_b_outbound_token --data-file=-
```

### 4.4. Dedicated service account

```bash
gcloud iam service-accounts create ticket-bridge-sa \
    --display-name="Ticket Bridge Cloud Run"

# Access to Cloud SQL
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:ticket-bridge-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/cloudsql.client"

# Access to secrets
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:ticket-bridge-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
```

### 4.5. Build and deploy to Cloud Run

```bash
# Build the image (Artifact Registry)
gcloud artifacts repositories create ticket-bridge \
    --repository-format=docker --location=europe-west1

gcloud builds submit --tag \
    europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/ticket-bridge/app:latest

# Deploy
gcloud run deploy ticket-bridge \
    --image=europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/ticket-bridge/app:latest \
    --region=europe-west1 \
    --service-account=ticket-bridge-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
    --add-cloudsql-instances=YOUR_PROJECT_ID:europe-west1:ticket-bridge-db \
    --set-env-vars="ENVIRONMENT=production,GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID,SYNC_SCHEDULER_ENABLED=false" \
    --set-env-vars="DATABASE_URL=postgresql://ticketbridge:PASSWORD@/ticketbridge?host=/cloudsql/YOUR_PROJECT_ID:europe-west1:ticket-bridge-db" \
    --set-secrets="SCHEDULER_SHARED_SECRET=scheduler-shared-secret:latest" \
    --no-allow-unauthenticated \
    --min-instances=0 \
    --max-instances=3
```

> **Note on the DB password in `DATABASE_URL`**: for real production use,
> prefer mounting the full `DATABASE_URL` via `--set-secrets` as well
> instead of passing it in `--set-env-vars`, or use the
> [Cloud SQL Python Connector](https://cloud.google.com/sql/docs/postgres/connect-connectors)
> instead of a connection string with an embedded password.

### 4.6. Cloud Scheduler (triggers `/sync`) - only needed on Cloud Run

**Skip this section if you're not deploying to Cloud Run.** The
in-process scheduler (section 3.4) already triggers sync automatically
for any deployment that runs as a continuous process (a VM, a persistent
container). Cloud Scheduler is only needed here because Cloud Run can
scale an instance to zero between requests, so it can't be relied on to
run a background job on its own - `SYNC_SCHEDULER_ENABLED` should be set
to `false` on Cloud Run to avoid pointless scheduler start/stop churn on
every cold start.

```bash
# Give the Scheduler an identity that can invoke the Cloud Run service
gcloud iam service-accounts create ticket-bridge-scheduler \
    --display-name="Ticket Bridge Scheduler Invoker"

gcloud run services add-iam-policy-binding ticket-bridge \
    --region=europe-west1 \
    --member="serviceAccount:ticket-bridge-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/run.invoker"

SERVICE_URL=$(gcloud run services describe ticket-bridge --region=europe-west1 --format='value(status.url)')

gcloud scheduler jobs create http ticket-bridge-sync \
    --location=europe-west1 \
    --schedule="*/2 * * * *" \
    --uri="${SERVICE_URL}/api/v1/sync" \
    --http-method=POST \
    --oidc-service-account-email="ticket-bridge-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --headers="X-Scheduler-Secret=VALUE_OF_scheduler-shared-secret"
```

> **`/sync` security**: the example above combines Cloud Scheduler's native
> OIDC (`--no-allow-unauthenticated` on Cloud Run + `--oidc-service-account-email`)
> with the shared secret header, as defense in depth. In many cases OIDC
> alone is already sufficient; the shared secret is a simple extra layer to
> maintain.

### 4.7. Registering the real systems

After deployment, access the frontend at `${SERVICE_URL}/` (authenticated
via IAM — see the next section) and create the real systems in the
"Systems" tab.

Outbound authentication is one generic mechanism (CLAUDE.md Decision 9),
not a choice of types: if a system's `auth_config` has a `secret_ref`,
the resolved secret is placed into a header — `auth_config.header`
(defaults to `X-API-Key`), optionally prefixed with
`auth_config.value_prefix`. The "Auth header name" / "Auth value prefix" /
"Secret reference" fields on the Systems tab map directly onto this.
Common patterns:

| Pattern | Header name | Value prefix |
|---|---|---|
| Custom API key header (default) | *(leave blank → `X-API-Key`)* | *(leave blank)* |
| Standard bearer token | `Authorization` | `Bearer ` (with a trailing space) |
| Some other custom scheme | whatever the destination expects | whatever prefix it expects, if any |

There's no built-in HTTP Basic Auth support — it needs a real
`base64(username:password)` encoding step this project doesn't implement
(see CLAUDE.md Decision 9); OAuth2 token exchange and HMAC request
signing aren't supported either, for the same reason (not a config
option, would need new code in `dispatcher.py`).

---

## 5. Configuration frontend security

The configuration (`/api/v1/systems`) and audit (`/api/v1/conversations`,
`/api/v1/audit`) endpoints **have no authentication of their own** in the
code — Cloud Run is deployed with `--no-allow-unauthenticated`, so only
principals with the `roles/run.invoker` role can call the service.

For human access to the frontend, the simplest options are:
- **Identity-Aware Proxy (IAP)** in front of Cloud Run — recommended, gives
  login with a corporate Google account with no extra code.
- An authenticated tunnel via `gcloud run services proxy ticket-bridge` for
  occasional administrative access without publicly exposing the service.

The `/api/v1/events` (called by external systems) and `/api/v1/sync`
(called by the Scheduler) endpoints have their own authentication (per-system
API key / scheduler secret), independent of Cloud Run IAM — so even with
`--no-allow-unauthenticated` it may be necessary to evaluate case by case
whether those systems can also authenticate via IAM, or whether they need
`--allow-unauthenticated` with application-level authentication (API key)
as the only barrier.

---

## 6. Day-to-day operation

- **Adding a new system (e.g. a 3rd team)**: "Systems" tab in the frontend
  → "New system". Requires no code deployment.
- **Adding a new topic / changing subscriptions**: "Topics" tab to create a
  category; check/uncheck it in a system's dialog on the "Systems" tab to
  change what that system receives. Both are no-deploy configuration
  changes - unsubscribing takes effect immediately (see CLAUDE.md
  Decision 8).
- **Diagnosing a failed delivery**: "Audit" tab, filter by system;
  `delivery_failure` entries show the HTTP/network error. The corresponding
  row in the `outbox` table stays `pending` until `max_attempts`, at which
  point it becomes `failed` for manual intervention.
- **Manually retrying a failed delivery**:
  ```sql
  UPDATE outbox SET status = 'pending', attempts = 0
  WHERE id = <row_id>;
  ```
  The next `/sync` run will retry it.

---

## 7. Continuous Integration / Continuous Delivery

Two GitHub Actions workflows live in `.github/workflows/`:

- **`tests.yml`** — runs `uv run pytest tests/ -v` on every push to `main`
  and on every pull request targeting `main`. This is the "Tests" badge at
  the top of this file.
- **`docker-publish.yml`** — on every push to `main` (and on tags matching
  `X.Y.Z`, no `v` prefix), runs the test suite again as a gate, then builds
  the image from the `Dockerfile` and pushes it to the
  [GitHub Container Registry](https://github.com/alexsantos/ticket-bridge/pkgs/container/ticket-bridge)
  (`ghcr.io/alexsantos/ticket-bridge`). Pushes to `main` are tagged
  `latest` and with the commit SHA; version tags additionally get a
  semver tag. This is the "Docker image" badge at the top of this file.
  On a tag push, it also creates a GitHub Release (see below).

Both workflows authenticate with the repository's built-in `GITHUB_TOKEN` -
no extra secrets need to be configured. The GHCR package's visibility
(public/private) is managed from the repository's **Packages** settings on
GitHub, independently of these workflows.

To deploy a published image to Cloud Run instead of building it locally
(see section 4.5), point `gcloud run deploy` at
`--image=ghcr.io/alexsantos/ticket-bridge:latest`; Cloud Run can pull from
GHCR directly if the package is public, or via a registry credential if
it's kept private.

### 7.1. Cutting a release

`pyproject.toml`'s `version` field is the single source of truth - the
git tag must match it exactly (no `v` prefix), or the workflow fails
before building or releasing anything:

```bash
# 1. Bump the version in pyproject.toml, then commit it
git commit -am "Bump version to 0.2.0"

# 2. Tag with the exact same version and push the tag
git tag 0.2.0
git push origin main --tags
```

This triggers `docker-publish.yml`'s tag path: tests → validate the tag
matches `pyproject.toml` → build and push `ghcr.io/alexsantos/ticket-bridge:0.2.0`
(and `:latest`) → create a GitHub Release named `0.2.0` with
auto-generated notes (the commit list since the previous tag) and a link
to the published image.

---

## 8. Suggested next steps (out of scope for this skeleton)

- Human authentication for the frontend (IAP).
- Alerts (e.g. Telegram, similar to other internal projects) when outbox
  entries reach `status = 'failed'`.
- Pagination on `GET /api/v1/conversations` for higher production volumes
  (`GET /api/v1/audit` already paginates - `limit`/`offset`, response
  wrapped in `{items, limit, offset, has_more}`, see the "Audit" tab).
- Webhook signature validation (HMAC) instead of just an API key, if any
  external system supports it.

## License

MIT — see [LICENSE](LICENSE).
