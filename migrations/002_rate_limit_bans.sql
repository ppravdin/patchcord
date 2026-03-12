-- Persistent rate-limit bans (survive server restarts)
-- Stores a SHA-256 hash of the banned token, not the token itself.

CREATE TABLE IF NOT EXISTS rate_limit_bans (
    token_hash TEXT PRIMARY KEY,
    banned_until TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for loading active bans on startup
CREATE INDEX IF NOT EXISTS idx_rate_limit_bans_until ON rate_limit_bans(banned_until);
