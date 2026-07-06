-- =============================================================================
-- 002_seed_example.sql
--
-- Dados de exemplo, úteis apenas em ambiente de desenvolvimento/teste.
-- NÃO correr em produção. Cria dois sistemas fictícios para permitir testar
-- o fluxo de ponta a ponta localmente antes de configurar os sistemas reais.
-- =============================================================================

BEGIN;

INSERT INTO systems (codigo, nome, base_url, auth_type, auth_config, status_mapping, payload_template, active)
VALUES
(
    'sistema_a',
    'ServiceDesk Equipa Clínica (exemplo)',
    'https://sistema-a.exemplo.local/api/tickets/webhook',
    'api_key',
    '{"header": "X-API-Key", "secret_ref": "sistema_a_outbound_key"}',
    '{
        "novo": "Aberto",
        "em_progresso": "Em Curso",
        "aguarda_terceiros": "Pendente",
        "resolvido": "Resolvido",
        "fechado": "Fechado"
    }',
    '{"ticket_ref": "{ref_externa}", "estado": "{status_mapeado}", "conversation_id": "{conversation_id}"}',
    TRUE
),
(
    'sistema_b',
    'ITSM Equipa Infraestrutura (exemplo)',
    'https://sistema-b.exemplo.local/api/v2/incidents/hook',
    'bearer',
    '{"secret_ref": "sistema_b_outbound_token"}',
    '{
        "novo": "NEW",
        "em_progresso": "IN_PROGRESS",
        "aguarda_terceiros": "WAITING",
        "resolvido": "RESOLVED",
        "fechado": "CLOSED"
    }',
    '{"incident_id": "{ref_externa}", "status": "{status_mapeado}", "correlation": "{conversation_id}"}',
    TRUE
);

-- Chaves de entrada de exemplo (hash de "dev-key-sistema-a" / "dev-key-sistema-b" em SHA-256).
-- Gerar chaves reais com: python -c "import secrets,hashlib; k=secrets.token_urlsafe(32); print(k, hashlib.sha256(k.encode()).hexdigest())"
INSERT INTO api_keys (sistema, key_hash, descricao)
VALUES
('sistema_a', 'a3f1c9e4d2b5f6a8c7e9b1d3f5a7c9e1b3d5f7a9c1e3b5d7f9a1c3e5b7d9f1a3', 'Chave de desenvolvimento - NÃO USAR EM PRODUÇÃO'),
('sistema_b', 'b4e2d0f5c3a6f7b9d8f0c2e4f6b8d0f2c4e6a8f0d2c4e6b8f0a2c4e6d8f0a2c4', 'Chave de desenvolvimento - NÃO USAR EM PRODUÇÃO');

COMMIT;
