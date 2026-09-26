# Ticket Bridge Simulator

A small stand-in for an external support system, for exercising Ticket
Bridge end to end when you don't have (enough) real systems connected -
e.g. a dev environment with only one real system. It does what a real
integrating system does, through the same public contract (root
README.md, "Integration contract"):

- **Send tickets**: open a ticket or reply on one - the simulator calls
  `POST /api/v1/events` with its own API key.
- **Receive tickets**: the bridge delivers `ticket.created` /
  `ticket.updated` to the simulator's `POST /webhook`, and each ticket
  shows the whole exchange as a chat-like timeline, `metadata` included.
- **Config page**: bridge URL, this system's API key, its ticket ref
  prefix, default topic, and optional webhook authentication.

It's independent of the bridge's code on purpose: its own dependencies,
its own copy of the contract (`sim/contract.py`), and no imports from
`app/` - so it behaves like any other integrating team's adapter would.
State (config, tickets, messages) is a SQLite file in `SIM_DATA_DIR`.

## 1. Register it in the bridge

The compose file runs **two** simulators, `simulator-a` and `simulator-b`,
so you can play both sides of a conversation (or use just one next to a
real system). Register each one you use as its own system in the bridge's
admin panel, **Systems → + New system**:

| | simulator-a | simulator-b |
|---|---|---|
| Code | e.g. `simulator_a` | e.g. `simulator_b` |
| Base URL | `http://simulator-a:8090/webhook` | `http://simulator-b:8090/webhook` |
| Topics | the topic(s) to exchange tickets on - the same one on both | same |

(Running directly on your machine instead of in compose, the Base URL is
`http://localhost:<its port>/webhook`.) Save, reopen it, and **generate
an inbound API key** - copy it, it's shown once. Each simulator gets its
own key.

Before 0.8.1 there was a single service called `simulator`; `simulator-a`
keeps that name as a network alias and uses the same data volume, so a
system registered as `http://simulator:8090/webhook` keeps working.

## 2. Run it

**With docker compose, next to the bridge** - no repo checkout needed,
just the repo's `docker-compose.yml` (and `.env`) on the host:

```bash
docker compose --profile simulator pull
docker compose --profile simulator up -d --remove-orphans
```

(`--remove-orphans` stops the pre-0.8.1 `simulator` container if it's
still running, which would otherwise keep port 8090.) To run only one of
them: `docker compose --profile simulator up -d simulator-a`.

The image, `ghcr.io/alexsantos/ticket-bridge-simulator`, is published by
the same workflow as the bridge's, with the same tags (`latest` from
`main`, `X.Y.Z` from version tags). From a repo checkout,
`docker compose --profile simulator up -d --build` builds it locally
instead. The first time the image is published, GHCR creates the package
as **private**: make it public in its GitHub package settings, or
`docker login ghcr.io` on the host.

They listen on the host's `127.0.0.1` only - `simulator-a` on port 8090,
`simulator-b` on 8091 - because the UI has no login. On a shared VM,
reach both through one SSH tunnel, then open http://localhost:8090 and
http://localhost:8091:

```bash
ssh -L 8090:localhost:8090 -L 8091:localhost:8091 <vm>
```

Each has its own data volume (`simulator_data` for A, `simulator_b_data`
for B), and compose seeds their system code and ticket ref prefix
(`SIMA-0001`, `SIMB-0001`) so the two are easy to tell apart.

**Directly, for local development:**

```bash
cd simulator
uv run uvicorn sim.main:app --port 8090
```

## 3. Configure it

In each simulator's **Config** tab, paste its own API key, check the
system code matches what you registered, and set a default topic. The
bridge URL is already `http://app:8080` under compose (set it to e.g.
`http://localhost:8080` when running directly). **Test saved connection** checks the bridge is reachable;
the API key itself is only checked when you send a ticket (there's no
side-effect-free way to validate it).

Every setting can also be seeded from the environment on first start
(`SIM_BRIDGE_URL`, `SIM_SYSTEM_CODE`, `SIM_API_KEY`, `SIM_REF_PREFIX`,
`SIM_DEFAULT_TOPIC`, `SIM_INBOUND_HEADER`, `SIM_INBOUND_SECRET`); once
saved from the Config page, the saved value wins.

**Webhook authentication (optional)**: if the bridge is configured to send
a secret to this system (the "Bridge → this system" group in its Systems
dialog: header name, value prefix, secret), set the same header name and
the exact value it sends (prefix included, e.g. `Bearer abc123`). Deliveries
that don't match get a 401, which shows up in the bridge's audit log as a
delivery failure - handy for testing that config. Leave it empty to accept
every delivery.

## 4. Use it

- **+ New ticket** opens a conversation from the simulator's side (the
  system must be subscribed to that topic in the bridge).
- Tickets opened by other systems appear automatically (auto-refresh
  every 3 s), marked with a dot while you haven't looked at them.
- **Reply** on any ticket: status, a note (sent as `metadata.note`) and
  optional extra `metadata` as a JSON object.

A received ticket shows "Not linked yet" until you reply to it: until
this system posts on a conversation, the bridge doesn't know its ticket
ref and keeps delivering `ticket.created` for it. The simulator matches
those by `conversation_id`, so they land on the same ticket.
Received tickets don't show a topic - `topic_code` isn't part of the
outbound payload.

## Tests

```bash
cd simulator
uv run pytest -v
```

The bridge is faked at the httpx level; the webhook is called exactly as
the bridge's dispatcher calls it.
