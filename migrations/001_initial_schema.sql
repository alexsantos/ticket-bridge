-- =============================================================================
-- 001_initial_schema.sql
--
-- Initial Ticket Bridge migration.
-- Creates the data model that supports correlation across N systems
-- (fan-out), mandatory ticket categorization via topics and per-system
-- subscriptions, the outbox pattern for asynchronous delivery, and full
-- event auditing.
--
-- Run with: psql "$DATABASE_URL" -f migrations/001_initial_schema.sql
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto"; -- needed for gen_random_uuid()

-- -----------------------------------------------------------------------------
-- systems
--
-- Registers each external application participating in the federation (e.g.
-- system_a, system_b, system_c). All integration-specific configuration
-- (base URL, outbound auth type, status mapping) lives here, so that adding
-- a new system is a configuration change, not a code change.
-- -----------------------------------------------------------------------------
CREATE TABLE systems (
    code            TEXT PRIMARY KEY,               -- short identifier, e.g. 'system_a'
    name            TEXT NOT NULL,                   -- human-readable name, e.g. 'Clinical Team ServiceDesk'
    base_url        TEXT NOT NULL,                   -- outbound endpoint (where the bridge calls this system)
    auth_type       TEXT NOT NULL DEFAULT 'api_key', -- 'api_key' | 'bearer' | 'basic'
    auth_config     JSONB NOT NULL DEFAULT '{}',     -- referenced secret (do not store plaintext in prod, see README)
    status_mapping  JSONB NOT NULL DEFAULT '{}',     -- translates external vocabulary <-> internal vocabulary
    payload_template JSONB NOT NULL DEFAULT '{}',    -- shape of the payload expected by this system
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE systems IS 'External systems federated through the bridge (replacing the central OSTicket).';

-- -----------------------------------------------------------------------------
-- topics
--
-- Ticket categories/queues (e.g. INFRA, SPM, SALES) that every conversation
-- must declare on creation, and that systems subscribe to. This is what
-- drives fan-out: a system receives a ticket (and its updates) if and only
-- if it is subscribed to that ticket's topic - see
-- system_topic_subscriptions below and
-- correlation_service.list_fanout_destinations.
-- -----------------------------------------------------------------------------
CREATE TABLE topics (
    code            TEXT PRIMARY KEY,               -- short identifier, e.g. 'INFRA'
    name            TEXT NOT NULL,                   -- human-readable name, e.g. 'Infrastructure'
    description     TEXT,
    active          BOOLEAN NOT NULL DEFAULT TRUE,   -- gates whether new conversations may choose this topic
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE topics IS 'Ticket categories/queues (e.g. INFRA, SPM, SALES) that systems subscribe to.';

-- -----------------------------------------------------------------------------
-- system_topic_subscriptions
--
-- Associative table (system <-> topic). A system only receives fan-out for
-- topics it is subscribed to; unsubscribing takes effect immediately for
-- any future event, even on conversations it already participates in.
-- -----------------------------------------------------------------------------
CREATE TABLE system_topic_subscriptions (
    system_code     TEXT NOT NULL REFERENCES systems(code) ON DELETE CASCADE,
    topic_code      TEXT NOT NULL REFERENCES topics(code) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (system_code, topic_code)
);

CREATE INDEX idx_subscriptions_topic ON system_topic_subscriptions(topic_code);

-- -----------------------------------------------------------------------------
-- api_keys
--
-- Inbound keys: used to authenticate calls that EACH system makes to the
-- bridge on POST /api/v1/events. Only the hash is stored.
-- -----------------------------------------------------------------------------
CREATE TABLE api_keys (
    id          BIGSERIAL PRIMARY KEY,
    system_code TEXT NOT NULL REFERENCES systems(code) ON DELETE CASCADE,
    key_hash    TEXT NOT NULL,               -- sha256 of the key, never the plaintext key
    description TEXT,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ
);

CREATE INDEX idx_api_keys_system_code ON api_keys(system_code) WHERE active = TRUE;

-- -----------------------------------------------------------------------------
-- conversations
--
-- A "conversation" is the equivalent of the parent ticket that OSTicket
-- used to represent implicitly. It does not belong to any single system -
-- it is the neutral entity that ties them together.
-- -----------------------------------------------------------------------------
CREATE TABLE conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject         TEXT,                             -- free-text ticket title, e.g. 'Lab printer malfunction'
    topic_code      TEXT NOT NULL REFERENCES topics(code),  -- category/queue, immutable after creation
    overall_status  TEXT NOT NULL DEFAULT 'open',    -- aggregated internal state (common vocabulary)
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_conversations_topic_code ON conversations(topic_code);

-- -----------------------------------------------------------------------------
-- conversation_participants
--
-- Links a conversation to each system involved and stores the external
-- reference (the ticket ID in that system) and the last known status in
-- that system. A row only exists once that system has itself reported an
-- event for this conversation. Fan-out destinations are NOT read from this
-- table (see system_topic_subscriptions) - it is only used to know, per
-- destination, whether they already have a ticket linked (so the outbound
-- payload can distinguish "update your ticket" from "please open one").
-- -----------------------------------------------------------------------------
CREATE TABLE conversation_participants (
    conversation_id UUID NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    system_code     TEXT NOT NULL REFERENCES systems(code),
    external_ref    TEXT NOT NULL,           -- ticket ID in the external system
    local_status    TEXT,                    -- last known status, in that system's vocabulary
    joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, system_code)
);

CREATE INDEX idx_participants_external_ref ON conversation_participants(system_code, external_ref);

-- -----------------------------------------------------------------------------
-- outbox
--
-- Table-based transactional queue (outbox pattern). Each row represents
-- "this must be delivered to this system". It is processed periodically by
-- the /api/v1/sync endpoint (called by Cloud Scheduler), using
-- SELECT ... FOR UPDATE SKIP LOCKED for concurrency safety without needing
-- an external broker (RabbitMQ/Pub-Sub).
-- -----------------------------------------------------------------------------
CREATE TABLE outbox (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    destination     TEXT NOT NULL REFERENCES systems(code),
    source          TEXT NOT NULL REFERENCES systems(code),  -- who generated the event (prevents loops)
    payload         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | sent | failed
    attempts        INT NOT NULL DEFAULT 0,
    max_attempts    INT NOT NULL DEFAULT 5,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ
);

CREATE INDEX idx_outbox_pending ON outbox(status, created_at) WHERE status = 'pending';
CREATE INDEX idx_outbox_conversation ON outbox(conversation_id);

-- -----------------------------------------------------------------------------
-- audit_log
--
-- Append-only record of everything that happened: events received,
-- deliveries made, failures, configuration changes. This is the basis for
-- the "Audit" tab in the frontend and the primary diagnostic resource.
-- -----------------------------------------------------------------------------
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID REFERENCES conversations(conversation_id) ON DELETE SET NULL,
    system_code     TEXT REFERENCES systems(code),
    event_type      TEXT NOT NULL,   -- e.g. 'event_received', 'delivery_success', 'delivery_failure', 'config_changed'
    detail          JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_conversation ON audit_log(conversation_id);
CREATE INDEX idx_audit_created_at ON audit_log(created_at DESC);

-- -----------------------------------------------------------------------------
-- trigger: keep updated_at automatically up to date
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_systems_updated_at
    BEFORE UPDATE ON systems
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_topics_updated_at
    BEFORE UPDATE ON topics
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_participants_updated_at
    BEFORE UPDATE ON conversation_participants
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
