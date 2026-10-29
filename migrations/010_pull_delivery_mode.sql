-- =============================================================================
-- 010_pull_delivery_mode.sql
--
-- Lets a system collect its messages instead of receiving them on a
-- webhook - for a system that can't expose a URL the bridge can reach
-- (e.g. a cloud-hosted product that only makes outbound calls). See
-- CLAUDE.md Decision 18.
--
--   delivery_mode   'push' (default, unchanged): the bridge POSTs each
--                   message to base_url. 'pull': the bridge never calls it;
--                   the system reads its pending outbox rows from
--                   GET /api/v1/inbox and acknowledges them.
--   base_url        now optional - only a push system needs one.
--   last_polled_at  when a pull system last called the inbox, so the admin
--                   panel can warn when it stops collecting.
--
-- Existing systems stay 'push', so nothing changes until an admin switches
-- one.
--
-- Run with: psql "$DATABASE_URL" -f migrations/010_pull_delivery_mode.sql
-- =============================================================================

BEGIN;

ALTER TABLE systems
    ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'push',
    ADD COLUMN last_polled_at TIMESTAMPTZ,
    ALTER COLUMN base_url DROP NOT NULL,
    ADD CONSTRAINT chk_systems_delivery_mode CHECK (delivery_mode IN ('push', 'pull')),
    ADD CONSTRAINT chk_systems_push_needs_base_url CHECK (delivery_mode = 'pull' OR base_url IS NOT NULL);

COMMIT;
