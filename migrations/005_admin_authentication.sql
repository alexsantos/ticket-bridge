-- =============================================================================
-- 005_admin_authentication.sql
--
-- Human authentication for the admin/config frontend (Systems/Topics/
-- Conversations/Audit tabs and their backing API). Previously assumed to
-- sit behind Cloud Run IAP or an authentication proxy in front of the
-- service (CLAUDE.md's "What this skeleton assumes and leaves undecided");
-- the deployment target has since moved to a self-hosted VM/container
-- behind a plain nginx reverse proxy, so that external layer no longer
-- exists in practice. See CLAUDE.md Decision 11 and README.md section 5.
--
-- `users`/`sessions` mirror `api_keys`'s hash+revoke pattern from
-- 001_initial_schema.sql: only a bcrypt hash of the password, and only a
-- SHA-256 hash of the opaque session token, are ever persisted - see
-- app/security.py (hash_password/verify_password, generate_session_token).
--
-- Unlike 002_seed_example.sql (dev-only seed data), the default admin
-- account seeded below is required in every environment, including
-- production - there is no other bootstrap path into `users`. Change its
-- password immediately after first login (see README.md section 5).
--
-- Run with: psql "$DATABASE_URL" -f migrations/005_admin_authentication.sql
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- users
--
-- Human administrators of the configuration/audit frontend. Distinct from
-- `systems`/`api_keys` (machine-to-machine, inbound calls to
-- POST /api/v1/events) and from SCHEDULER_SHARED_SECRET (machine-to-machine,
-- POST /api/v1/sync) - this is the first human credential in the schema.
-- -----------------------------------------------------------------------------
CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,          -- bcrypt hash, never plaintext
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- -----------------------------------------------------------------------------
-- sessions
--
-- DB-backed session tokens for the admin frontend, sent back and forth as
-- an httpOnly cookie (see app/api/auth.py). Only the SHA-256 hash of the
-- opaque token is stored - the plaintext exists only in the Set-Cookie
-- response at login time, exactly like api_keys.key_hash for inbound keys.
-- Revocation (logout) is `active = FALSE, revoked_at = now()`, never a
-- hard delete - same convention as api_keys.
-- -----------------------------------------------------------------------------
CREATE TABLE sessions (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ
);

CREATE INDEX idx_sessions_token_hash ON sessions(token_hash) WHERE active = TRUE;
CREATE INDEX idx_sessions_user_id ON sessions(user_id) WHERE active = TRUE;

-- Seed the default admin account. Password: 'ChangeMe-Immediately!'
-- Hash computed offline with:
--   python -c "import bcrypt; print(bcrypt.hashpw(b'ChangeMe-Immediately!', bcrypt.gensalt()).decode())"
-- CHANGE THIS PASSWORD IMMEDIATELY after first login (POST
-- /api/v1/auth/change-password, or the "Change password" link in the
-- frontend footer) - it is a known value in git history, same as this
-- project's other seeded dev credentials.
INSERT INTO users (username, password_hash)
VALUES ('admin', '$2b$12$7vnVFaM7IeXK/QS2angmN.6TX7IMGuyc4zG9se4.b9HS8ygp1e0VG');

COMMIT;
