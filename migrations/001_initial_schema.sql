-- =============================================================================
-- 001_initial_schema.sql
--
-- Migration inicial do Ticket Bridge.
-- Cria o modelo de dados que suporta correlação de N sistemas (fan-out),
-- outbox pattern para entrega assíncrona e auditoria completa de eventos.
--
-- Correr com: psql "$DATABASE_URL" -f migrations/001_initial_schema.sql
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto"; -- necessário para gen_random_uuid()

-- -----------------------------------------------------------------------------
-- systems
--
-- Regista cada aplicação externa que participa na federação (ex: sistema_a,
-- sistema_b, sistema_c). Toda a configuração específica de integração
-- (URL base, tipo de autenticação de saída, mapeamento de estados) vive aqui,
-- para que adicionar um novo sistema seja configuração, não código.
-- -----------------------------------------------------------------------------
CREATE TABLE systems (
    codigo          TEXT PRIMARY KEY,               -- identificador curto, ex: 'sistema_a'
    nome            TEXT NOT NULL,                   -- nome legível, ex: 'ServiceDesk Equipa Clínica'
    base_url        TEXT NOT NULL,                   -- endpoint de saída (onde o bridge chama esse sistema)
    auth_type       TEXT NOT NULL DEFAULT 'api_key', -- 'api_key' | 'bearer' | 'basic'
    auth_config     JSONB NOT NULL DEFAULT '{}',     -- segredo referenciado (não guardar plaintext em prod, ver README)
    status_mapping  JSONB NOT NULL DEFAULT '{}',     -- traduz vocabulário externo <-> vocabulário interno
    payload_template JSONB NOT NULL DEFAULT '{}',    -- forma do payload esperado por este sistema
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE systems IS 'Sistemas externos federados através do bridge (substituem o OSTicket central).';

-- -----------------------------------------------------------------------------
-- api_keys
--
-- Chaves de entrada (inbound): usadas para autenticar chamadas que CADA
-- sistema faz ao bridge em POST /api/v1/events. Guardamos apenas o hash.
-- -----------------------------------------------------------------------------
CREATE TABLE api_keys (
    id          BIGSERIAL PRIMARY KEY,
    sistema     TEXT NOT NULL REFERENCES systems(codigo) ON DELETE CASCADE,
    key_hash    TEXT NOT NULL,               -- sha256 da chave, nunca a chave em texto simples
    descricao   TEXT,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ
);

CREATE INDEX idx_api_keys_sistema ON api_keys(sistema) WHERE active = TRUE;

-- -----------------------------------------------------------------------------
-- conversations
--
-- Uma "conversa" é o equivalente ao ticket-mãe que o OSTicket antes
-- representava implicitamente. Não pertence a nenhum sistema em concreto -
-- é a entidade neutra que os une.
-- -----------------------------------------------------------------------------
CREATE TABLE conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assunto         TEXT,
    status_geral    TEXT NOT NULL DEFAULT 'aberto',  -- estado interno agregado (vocabulário comum)
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- conversation_participants
--
-- Liga uma conversa a cada sistema envolvido e guarda a referência externa
-- (o ID do ticket nesse sistema) e o último status conhecido nesse sistema.
-- Esta tabela é o que permite fan-out para N sistemas em vez de um par fixo
-- A<->B: para notificar todos os interessados numa mudança basta fazer
-- SELECT sistema FROM conversation_participants WHERE conversation_id = ...
-- -----------------------------------------------------------------------------
CREATE TABLE conversation_participants (
    conversation_id UUID NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    sistema         TEXT NOT NULL REFERENCES systems(codigo),
    ref_externa     TEXT NOT NULL,           -- ID do ticket no sistema externo
    status_local    TEXT,                    -- último status conhecido, no vocabulário desse sistema
    joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, sistema)
);

CREATE INDEX idx_participants_ref_externa ON conversation_participants(sistema, ref_externa);

-- -----------------------------------------------------------------------------
-- outbox
--
-- Fila transacional baseada em tabela (outbox pattern). Cada linha representa
-- "isto tem de ser entregue a este sistema". É processada periodicamente pelo
-- endpoint /api/v1/sync (chamado pelo Cloud Scheduler), usando
-- SELECT ... FOR UPDATE SKIP LOCKED para segurança em concorrência sem
-- precisar de um broker externo (RabbitMQ/Pub-Sub).
-- -----------------------------------------------------------------------------
CREATE TABLE outbox (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    destino         TEXT NOT NULL REFERENCES systems(codigo),
    origem          TEXT NOT NULL REFERENCES systems(codigo),  -- quem gerou o evento (previne loops)
    payload         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | sent | failed
    tentativas      INT NOT NULL DEFAULT 0,
    max_tentativas  INT NOT NULL DEFAULT 5,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ
);

CREATE INDEX idx_outbox_pending ON outbox(status, created_at) WHERE status = 'pending';
CREATE INDEX idx_outbox_conversation ON outbox(conversation_id);

-- -----------------------------------------------------------------------------
-- audit_log
--
-- Registo append-only de tudo o que aconteceu: eventos recebidos, entregas
-- feitas, falhas, alterações de configuração. É a base do separador de
-- "Auditoria" no frontend e o principal recurso de diagnóstico.
-- -----------------------------------------------------------------------------
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID REFERENCES conversations(conversation_id) ON DELETE SET NULL,
    sistema         TEXT REFERENCES systems(codigo),
    evento_tipo     TEXT NOT NULL,   -- ex: 'evento_recebido', 'entrega_sucesso', 'entrega_falha', 'config_alterada'
    detalhe         JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_conversation ON audit_log(conversation_id);
CREATE INDEX idx_audit_created_at ON audit_log(created_at DESC);

-- -----------------------------------------------------------------------------
-- trigger: manter updated_at atualizado automaticamente
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_systems_updated_at
    BEFORE UPDATE ON systems
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_participants_updated_at
    BEFORE UPDATE ON conversation_participants
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
