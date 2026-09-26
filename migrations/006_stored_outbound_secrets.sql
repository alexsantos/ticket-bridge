-- =============================================================================
-- 006_stored_outbound_secrets.sql
--
-- Lets an admin set a system's outbound secret (the value the bridge sends
-- in auth_config.header when delivering to it) from the frontend, instead
-- of only by name via auth_config.secret_ref - which points at an
-- environment variable or Secret Manager, and so needs shell access to the
-- host to change. See CLAUDE.md Decision 13.
--
-- The value is stored encrypted (Fernet, keyed by SECRETS_ENCRYPTION_KEY
-- from the environment - app/services/secret_store.py), never in
-- plaintext, and the API never returns it. Unlike api_keys.key_hash it
-- can't be a hash: the bridge has to send the actual value.
--
-- When both are set, the stored secret takes precedence over secret_ref.
--
-- Run with: psql "$DATABASE_URL" -f migrations/006_stored_outbound_secrets.sql
-- =============================================================================

BEGIN;

ALTER TABLE systems ADD COLUMN outbound_secret_encrypted TEXT;

COMMIT;
