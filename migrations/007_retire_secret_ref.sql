-- =============================================================================
-- 007_retire_secret_ref.sql
--
-- auth_config.secret_ref (an outbound secret looked up by name in an
-- environment variable or Secret Manager) is no longer supported: the only
-- source of a system's outbound secret is the one stored encrypted from the
-- Systems tab (systems.outbound_secret_encrypted, migration 006). See
-- CLAUDE.md Decision 14.
--
-- Systems that already have a stored secret were using it already (it took
-- precedence), so their now-dead secret_ref is removed here. Systems that
-- still rely on a secret_ref are deliberately left alone: their deliveries
-- fail with an explanatory error, and the Systems tab flags them, until an
-- admin sets their outbound secret or marks them as having none - silently
-- dropping the reference would switch them to unauthenticated delivery.
--
-- Run with: psql "$DATABASE_URL" -f migrations/007_retire_secret_ref.sql
-- =============================================================================

BEGIN;

UPDATE systems
SET auth_config = auth_config - 'secret_ref'
WHERE outbound_secret_encrypted IS NOT NULL
  AND auth_config ? 'secret_ref';

COMMIT;
