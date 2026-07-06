-- =============================================================================
-- 002_seed_example.sql
--
-- Sample data, useful only in a development/test environment.
-- DO NOT run in production. Creates two fictitious systems to allow testing
-- the end-to-end flow locally before configuring the real systems.
-- =============================================================================

BEGIN;

INSERT INTO systems (code, name, base_url, auth_type, auth_config, status_mapping, payload_template, active)
VALUES
(
    'system_a',
    'Clinical Team ServiceDesk (example)',
    'https://system-a.example.local/api/tickets/webhook',
    'api_key',
    '{"header": "X-API-Key", "secret_ref": "system_a_outbound_key"}',
    '{
        "new": "Open",
        "in_progress": "Under Way",
        "waiting_third_party": "Awaiting Reply",
        "resolved": "Resolved",
        "closed": "Closed"
    }',
    '{"ticket_ref": "{external_ref}", "status": "{mapped_status}", "conversation_id": "{conversation_id}"}',
    TRUE
),
(
    'system_b',
    'Infrastructure Team ITSM (example)',
    'https://system-b.example.local/api/v2/incidents/hook',
    'bearer',
    '{"secret_ref": "system_b_outbound_token"}',
    '{
        "new": "NEW",
        "in_progress": "IN_PROGRESS",
        "waiting_third_party": "WAITING",
        "resolved": "RESOLVED",
        "closed": "CLOSED"
    }',
    '{"incident_id": "{external_ref}", "status": "{mapped_status}", "correlation": "{conversation_id}"}',
    TRUE
);

-- Sample inbound keys (SHA-256 hash of "dev-key-system-a" / "dev-key-system-b").
-- Generate real keys with: python -c "import secrets,hashlib; k=secrets.token_urlsafe(32); print(k, hashlib.sha256(k.encode()).hexdigest())"
INSERT INTO api_keys (system_code, key_hash, description)
VALUES
('system_a', 'a3f1c9e4d2b5f6a8c7e9b1d3f5a7c9e1b3d5f7a9c1e3b5d7f9a1c3e5b7d9f1a3', 'Development key - DO NOT USE IN PRODUCTION'),
('system_b', 'b4e2d0f5c3a6f7b9d8f0c2e4f6b8d0f2c4e6a8f0d2c4e6b8f0a2c4e6d8f0a2c4', 'Development key - DO NOT USE IN PRODUCTION');

COMMIT;