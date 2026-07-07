-- =============================================================================
-- 002_seed_example.sql
--
-- Sample data, useful only in a development/test environment.
-- DO NOT run in production. Creates two fictitious systems, four example
-- topics, and their subscriptions, to allow testing the end-to-end flow
-- (including proactive fan-out to a topic's subscribers) locally before
-- configuring the real systems.
--
-- PATIENT_ADMIN models this project's flagship use case: system_a (a
-- clinical team) and system_b (patient registration/insurance) exchange
-- updates on a patient's insurance verification almost like a chat, via
-- the shared conversation_id - see examples/README.md for the full
-- walkthrough. INFRA/SPM/SALES remain as simpler generic examples (see
-- README.md section 3.3).
-- =============================================================================

BEGIN;

INSERT INTO topics (code, name, description)
VALUES
    ('INFRA', 'Infrastructure', 'Network, servers, and workstation issues'),
    ('SPM', 'Service & Project Management', 'Project coordination and service requests'),
    ('SALES', 'Sales', 'Sales team requests and customer escalations'),
    ('PATIENT_ADMIN', 'Patient Administration', 'Patient registration, insurance verification, and admissions coordination. '
        || 'Expected metadata keys (not enforced by the bridge - see README.md "Integration contract"): '
        || 'note (string, free-text human comment, optional on every event) and '
        || 'insurance_number (string, set by system_b when resolving a coverage verification case).');

INSERT INTO systems (code, name, base_url, auth_config, active)
VALUES
(
    'system_a',
    'Clinical Team ServiceDesk (example)',
    'https://system-a.example.local/api/tickets/webhook',
    '{"header": "X-API-Key", "secret_ref": "system_a_outbound_key"}',
    TRUE
),
(
    'system_b',
    'Patient Registration & Insurance (example)',
    'https://system-b.example.local/api/v2/patient-cases/hook',
    '{"header": "Authorization", "value_prefix": "Bearer ", "secret_ref": "system_b_outbound_token"}',
    TRUE
);

-- system_a subscribes to INFRA and PATIENT_ADMIN; system_b subscribes to
-- INFRA, SPM, and PATIENT_ADMIN. This means creating a PATIENT_ADMIN
-- ticket as system_a immediately fans out to system_b (it's subscribed),
-- while a SALES ticket would reach neither.
INSERT INTO system_topic_subscriptions (system_code, topic_code)
VALUES
    ('system_a', 'INFRA'),
    ('system_a', 'PATIENT_ADMIN'),
    ('system_b', 'INFRA'),
    ('system_b', 'SPM'),
    ('system_b', 'PATIENT_ADMIN');

-- Sample inbound keys (SHA-256 hash of "dev-key-system-a" / "dev-key-system-b").
-- Generate real keys with: python -c "import secrets,hashlib; k=secrets.token_urlsafe(32); print(k, hashlib.sha256(k.encode()).hexdigest())"
INSERT INTO api_keys (system_code, key_hash, description)
VALUES
('system_a', 'f02cbb6e5afba5f5ed03f04691bb759b3c0e9a40e4fc2ff4a6cef40caaef09fc', 'Development key - DO NOT USE IN PRODUCTION'),
('system_b', 'c00e4e16d783e7b03680d0cb04ff052cf56c77292f00d41a6cd84c88cec2dab1', 'Development key - DO NOT USE IN PRODUCTION');

COMMIT;
