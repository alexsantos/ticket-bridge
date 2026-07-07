-- =============================================================================
-- 003_standardize_ticket_status.sql
--
-- Replaces per-system status translation and payload templates with one
-- canonical, enforced status vocabulary and one fixed outbound payload
-- shape - see CLAUDE.md Decision 4 (rewritten) and Decision 8's payload
-- addendum. Every integrating system now speaks the bridge's canonical
-- vocabulary directly (new/in_progress/waiting_third_party/resolved/
-- closed) instead of the bridge adapting to each system's own labels.
--
-- This is a breaking change to the wire contract, not just internal
-- config - see README.md's "Integration contract" section.
--
-- Note: `outbox.status` (pending|sent|failed) and other unconstrained
-- text columns have the same latent "no CHECK constraint" gap as
-- overall_status/local_status did, but are intentionally left untouched
-- here - different vocabulary, different owner (outbox_service.py),
-- unrelated to this ticket-status pivot.
--
-- Run with: psql "$DATABASE_URL" -f migrations/003_standardize_ticket_status.sql
-- =============================================================================

BEGIN;

ALTER TABLE systems
    DROP COLUMN status_mapping,
    DROP COLUMN payload_template;

-- overall_status's old DEFAULT 'open' was never actually relied on -
-- correlation_service.find_or_create_conversation's INSERT always
-- supplies it explicitly - and 'open' isn't even a canonical value.
-- Dropping the default (rather than fixing it to a valid one) means any
-- future insert that omits it fails loudly instead of silently bypassing
-- the CHECK constraint being added below via a default value.
ALTER TABLE conversations
    ALTER COLUMN overall_status DROP DEFAULT;

ALTER TABLE conversations
    ADD CONSTRAINT chk_conversations_overall_status
    CHECK (overall_status IN ('new', 'in_progress', 'waiting_third_party', 'resolved', 'closed'));

ALTER TABLE conversation_participants
    ADD CONSTRAINT chk_participants_local_status
    CHECK (local_status IS NULL OR local_status IN ('new', 'in_progress', 'waiting_third_party', 'resolved', 'closed'));

COMMIT;
