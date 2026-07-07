-- =============================================================================
-- 004_unify_auth_mechanism.sql
--
-- Collapses the api_key/bearer/basic auth_type enum into one generic
-- "secret in a header" mechanism, configured entirely via auth_config
-- (an optional `header` name and an optional `value_prefix`) - see
-- CLAUDE.md Decision 9. Basic Auth is also dropped: it required
-- auth_config's secret to already be pre-base64-encoded "user:pass" with
-- nothing surfacing or explaining that requirement - a real usability
-- gap, not a deliberate design choice worth keeping.
--
-- Run with: psql "$DATABASE_URL" -f migrations/004_unify_auth_mechanism.sql
-- =============================================================================

BEGIN;

ALTER TABLE systems DROP COLUMN auth_type;

COMMIT;
