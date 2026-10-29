-- =============================================================================
-- 009_overall_status_follows_latest_event.sql
--
-- conversations.overall_status used to be written once, when a conversation
-- was created, and never again - so every conversation read "new" forever.
-- From this version each event on an existing conversation sets it to that
-- event's status (correlation_service.find_or_create_conversation).
--
-- This backfills existing conversations: overall_status becomes the status
-- of their latest received event (audit_log 'event_received'), and
-- updated_at the time of that event - the updated_at trigger is paused so
-- the backfill itself doesn't make every conversation look active "now".
--
-- Safe to run more than once.
--
-- Run with: psql "$DATABASE_URL" -f migrations/009_overall_status_follows_latest_event.sql
-- =============================================================================

BEGIN;

ALTER TABLE conversations DISABLE TRIGGER trg_conversations_updated_at;

UPDATE conversations c
SET overall_status = latest.status,
    updated_at = GREATEST(c.updated_at, latest.created_at)
FROM (
    SELECT DISTINCT ON (conversation_id)
        conversation_id, detail->>'status' AS status, created_at
    FROM audit_log
    WHERE event_type = 'event_received' AND conversation_id IS NOT NULL
    ORDER BY conversation_id, id DESC
) latest
WHERE latest.conversation_id = c.conversation_id
  AND latest.status IN ('new', 'in_progress', 'waiting_third_party', 'resolved', 'closed')
  AND (c.overall_status IS DISTINCT FROM latest.status OR c.updated_at < latest.created_at);

ALTER TABLE conversations ENABLE TRIGGER trg_conversations_updated_at;

COMMIT;
