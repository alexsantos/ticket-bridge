# Ticket Bridge

[![Tests](https://github.com/alexsantos/ticket-bridge/actions/workflows/tests.yml/badge.svg)](https://github.com/alexsantos/ticket-bridge/actions/workflows/tests.yml)
[![Docker image](https://github.com/alexsantos/ticket-bridge/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/alexsantos/ticket-bridge/pkgs/container/ticket-bridge)

Lightweight ticket correlation service across multiple support applications,
designed to replace the "central hub" role that OSTicket used to play
implicitly when two (or more) teams used the same tool to track processes
between each other.

Each team keeps using its own operational support application. Ticket
Bridge sits in the middle: it receives creation/update events from one
system, maintains the correlation (`conversation_id`), and distributes
("fans out") the status change to every other system involved in that
conversation.

See **`CLAUDE.md`** for the full rationale behind the architecture decisions.

---

## 1. Architecture overview

```
System A ──POST /api/v1/events──▶ ┌────────────────────┐
                                    │   Ticket Bridge     │
System B ──POST /api/v1/events──▶ │  (Cloud Run, FastAPI)│
                                    │                      │
System C ──POST /api/v1/events──▶ │  PostgreSQL:         │
                                    │   - conversations     │
                                    │   - participants       │
            ◀── outbox (HTTP) ──── │   - outbox (queue)       │
                                    │   - audit_log            │
                                    └──────────┬───────────┘
                                               │
                                     Cloud Scheduler
                                     (triggers /api/v1/sync
                                      every 1-2 min)
```

- **No external broker** (RabbitMQ/Pub-Sub): the queue is a Postgres table
  (`outbox`), processed with `SELECT ... FOR UPDATE SKIP LOCKED`.
- **Stateless**: any Cloud Run instance can process any request; all state
  lives in the database.
- **N systems, not just 2**: `conversation_participants` allows associating
  as many systems as needed with the same conversation.

---

## 2. Project structure

```
ticket-bridge/
├── app/
│   ├── main.py                  # app startup, routers, health check
│   ├── config.py                # configuration via environment variables
│   ├── database.py              # Postgres connection pool (psycopg3)
│   ├── models.py                # internal domain models
│   ├── schemas.py                # API request/response contracts
│   ├── security.py              # authentication (API keys, scheduler secret)
│   ├── api/
│   │   ├── events.py            # POST /api/v1/events   (inbound)
│   │   ├── sync.py              # POST /api/v1/sync     (processes outbox)
│   │   ├── systems.py           # CRUD /api/v1/systems  (configuration)
│   │   ├── conversations.py     # GET  /api/v1/conversations
│   │   └── audit.py             # GET  /api/v1/audit
│   ├── services/
│   │   ├── correlation_service.py  # conversation/participant management
│   │   ├── status_mapper.py        # status vocabulary translation
│   │   ├── outbox_service.py       # table-based transactional queue (outbox pattern)
│   │   ├── dispatcher.py           # HTTP delivery to each system
│   │   ├── secrets.py              # secret resolution (Secret Manager / env)
│   │   └── audit_service.py        # audit_log read/write
│   └── frontend/
│       ├── index.html           # configuration/audit panel
│       ├── style.css
│       └── app.js
├── migrations/
│   ├── 001_initial_schema.sql   # full schema (run first)
│   └── 002_seed_example.sql     # sample data (development only)
├── tests/
│   └── test_status_mapper.py
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

# 4. Run the migrations
psql "postgresql://localhost/ticketbridge" -f migrations/001_initial_schema.sql
psql "postgresql://localhost/ticketbridge" -f migrations/002_seed_example.sql   # optional, sample data

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

```bash
# Create a conversation from "system_a" (uses the seed's sample key)
curl -X POST http://localhost:8080/api/v1/events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-system-a" \
  -d '{"external_ref": "TICKET-123", "status": "new", "subject": "Lab printer malfunction"}'

# Note: since "system_a" has no other participants in the conversation yet,
# nothing is sent to the outbox on this first call - a second system needs
# to join the same conversation_id returned in the response above.
```

To run the automated tests:
```bash
uv run pytest tests/ -v
```

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
# (002_seed_example.sql is for development only - do not run in production)
```

### 4.3. Secrets (Secret Manager)

Each external system has a secret reference (`secret_ref`) in its
configuration — it's the value the dispatcher uses to authenticate
outbound calls. The Cloud Scheduler shared secret and the DB password
should also live here.

```bash
echo -n "DB_PASSWORD" | gcloud secrets create db-password --data-file=-
echo -n "$(openssl rand -base64 32)" | gcloud secrets create scheduler-shared-secret --data-file=-

# One secret per external system, name matching the 'secret_ref' configured
# in the frontend for that system:
echo -n "SYSTEM_A_OUTBOUND_KEY" | gcloud secrets create system_a_outbound_key --data-file=-
echo -n "SYSTEM_B_OUTBOUND_TOKEN" | gcloud secrets create system_b_outbound_token --data-file=-
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
    --set-env-vars="ENVIRONMENT=production,GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID" \
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

### 4.6. Cloud Scheduler (triggers `/sync`)

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
"Systems" tab, with `secret_ref` pointing to the secrets created in 4.3.

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
  `v*.*.*`), runs the test suite again as a gate, then builds the image
  from the `Dockerfile` and pushes it to the
  [GitHub Container Registry](https://github.com/alexsantos/ticket-bridge/pkgs/container/ticket-bridge)
  (`ghcr.io/alexsantos/ticket-bridge`). Pushes to `main` are tagged
  `latest` and with the commit SHA; version tags additionally get a
  semver tag. This is the "Docker image" badge at the top of this file.

Both workflows authenticate with the repository's built-in `GITHUB_TOKEN` -
no extra secrets need to be configured. The GHCR package's visibility
(public/private) is managed from the repository's **Packages** settings on
GitHub, independently of these workflows.

To deploy a published image to Cloud Run instead of building it locally
(see section 4.5), point `gcloud run deploy` at
`--image=ghcr.io/alexsantos/ticket-bridge:latest`; Cloud Run can pull from
GHCR directly if the package is public, or via a registry credential if
it's kept private.

---

## 8. Suggested next steps (out of scope for this skeleton)

- Human authentication for the frontend (IAP).
- Alerts (e.g. Telegram, similar to other internal projects) when outbox
  entries reach `status = 'failed'`.
- Pagination on the listing endpoints (`conversations`, `audit`) for higher
  production volumes.
- Webhook signature validation (HMAC) instead of just an API key, if any
  external system supports it.
