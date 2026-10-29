-- =============================================================================
-- 011_archive_and_delete_systems.sql
--
-- Systems that are no longer used can be archived (hidden, unable to send
-- or receive, history kept) or - if they never exchanged a message -
-- deleted. See CLAUDE.md Decision 19.
--
--   systems.archived_at   set while a system is archived.
--
--   audit_log.system_code loses its foreign key to systems. The audit log
--                         is append-only history: it must outlive what it
--                         describes, and every system has audit rows from
--                         its own creation and keys, which would otherwise
--                         block deleting even an unused one. Rows keep the
--                         code as plain text.
--
-- conversation_participants and outbox keep their foreign keys on purpose:
-- a system with messages can't be deleted, only archived.
--
-- Run with: psql "$DATABASE_URL" -f migrations/011_archive_and_delete_systems.sql
-- =============================================================================

BEGIN;

ALTER TABLE systems ADD COLUMN archived_at TIMESTAMPTZ;

DO $$
DECLARE
    fk TEXT;
BEGIN
    SELECT c.conname INTO fk
    FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
    WHERE c.conrelid = 'audit_log'::regclass AND c.contype = 'f' AND a.attname = 'system_code';
    IF fk IS NOT NULL THEN
        EXECUTE format('ALTER TABLE audit_log DROP CONSTRAINT %I', fk);
    END IF;
END $$;

COMMIT;
