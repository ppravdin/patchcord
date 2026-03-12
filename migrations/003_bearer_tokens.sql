-- Bearer token storage (replaces env var token configuration)
-- Tokens are stored as SHA-256 hashes; plaintext is never persisted.

CREATE TABLE IF NOT EXISTS bearer_tokens (
    token_hash TEXT PRIMARY KEY,
    namespace_id TEXT NOT NULL DEFAULT 'default',
    agent_id TEXT NOT NULL,
    label TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bearer_tokens_agent
    ON bearer_tokens(namespace_id, agent_id);
