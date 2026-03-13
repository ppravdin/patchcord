-- User-to-namespace ownership mapping.
-- A user owns multiple namespaces; all agents across a user's namespaces
-- form one trust zone and can communicate freely.
-- Cross-user access is always blocked.

CREATE TABLE IF NOT EXISTS user_namespaces (
    user_id TEXT NOT NULL,
    namespace_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, namespace_id)
);

CREATE INDEX IF NOT EXISTS idx_user_namespaces_namespace
    ON user_namespaces(namespace_id);
