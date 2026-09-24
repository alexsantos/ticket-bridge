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

In the bridge's admin panel, **Systems → + New system**:

| Field     | Value |
|-----------|-------|
| Code      | e.g. `simulator` |
| Base URL  | `http://simulator:8090/webhook` under docker compose, or `http://localhost:8090/webhook` when both run directly on your machine |
| Topics    | the topic(s) you want to exchange tickets on |

Save, reopen it, and **generate an inbound API key** - copy it, it's shown
once. (Or: `./examples/dummy_system.sh setup <topic>` with
`DUMMY_CODE=simulator DUMMY_BASE_URL=http://simulator:8090/webhook`, then
read the key from `examples/.dummy-simulator.key`.)

## 2. Run it

**With docker compose, next to the bridge** (from the repo root):

```bash
docker compose --profile simulator up -d --build
```

It's built from `./simulator` (no published image) and listens on
`127.0.0.1:8090` only - the UI has no login, so on a shared VM reach it
through an SSH tunnel (`ssh -L 8090:localhost:8090 <vm>`), then open
http://localhost:8090. Its data lives in the `simulator_data` volume.

**Directly, for local development:**

```bash
cd simulator
uv run uvicorn sim.main:app --port 8090
```

## 3. Configure it

Open the **Config** tab, paste the API key, set the bridge URL
(`http://app:8080` under compose - already the default there - or
e.g. `http://localhost:8080`), the system code you registered, and a
default topic. **Test saved connection** checks the bridge is reachable;
the API key itself is only checked when you send a ticket (there's no
side-effect-free way to validate it).

Every setting can also be seeded from the environment on first start
(`SIM_BRIDGE_URL`, `SIM_SYSTEM_CODE`, `SIM_API_KEY`, `SIM_REF_PREFIX`,
`SIM_DEFAULT_TOPIC`, `SIM_INBOUND_HEADER`, `SIM_INBOUND_SECRET`); once
saved from the Config page, the saved value wins.

**Webhook authentication (optional)**: if the bridge is configured to send
a secret to this system (its `auth_config`: header, value prefix,
`secret_ref` - CLAUDE.md Decision 9), set the same header name and the
exact value it sends (prefix included, e.g. `Bearer abc123`). Deliveries
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
