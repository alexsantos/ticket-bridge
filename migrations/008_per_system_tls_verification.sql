-- =============================================================================
-- 008_per_system_tls_verification.sql
--
-- Lets an admin turn off TLS certificate verification for one system's
-- outbound deliveries - for a destination whose Base URL serves a
-- self-signed (or otherwise untrusted) certificate, which httpx otherwise
-- rejects with a certificate verification error. See CLAUDE.md Decision 15.
--
-- Per system, not global: every other destination keeps full verification.
-- Defaults to TRUE, so existing systems are unaffected.
--
-- Run with: psql "$DATABASE_URL" -f migrations/008_per_system_tls_verification.sql
-- =============================================================================

BEGIN;

ALTER TABLE systems ADD COLUMN verify_tls BOOLEAN NOT NULL DEFAULT TRUE;

COMMIT;
