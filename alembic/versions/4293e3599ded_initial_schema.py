"""initial schema

Revision ID: 4293e3599ded
Revises:
Create Date: 2026-03-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '4293e3599ded'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_messages (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            namespace_id TEXT NOT NULL DEFAULT 'default',
            from_agent TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            content TEXT NOT NULL,
            reply_to UUID REFERENCES agent_messages(id),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'read', 'replied', 'deferred')),
            encrypted BOOLEAN DEFAULT FALSE,
            delivered_at TIMESTAMPTZ,
            group_id UUID,
            recipients TEXT[],
            topic TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_messages_ns_to_status ON agent_messages(namespace_id, to_agent, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_messages_ns_reply_to ON agent_messages(namespace_id, reply_to)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_messages_topic ON agent_messages(topic) WHERE topic IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_messages_group_id ON agent_messages(group_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_registry (
            namespace_id TEXT NOT NULL DEFAULT 'default',
            agent_id TEXT NOT NULL,
            display_name TEXT,
            machine_name TEXT NOT NULL DEFAULT 'unknown',
            status TEXT NOT NULL DEFAULT 'online'
                CHECK (status IN ('online', 'offline')),
            last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            meta JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (namespace_id, agent_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_registry_ns_status_seen ON agent_registry(namespace_id, status, last_seen DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_registry_seen ON agent_registry(last_seen DESC)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS bearer_tokens (
            token_hash TEXT PRIMARY KEY,
            namespace_id TEXT NOT NULL DEFAULT 'default',
            agent_id TEXT NOT NULL,
            label TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_bearer_tokens_agent ON bearer_tokens(namespace_id, agent_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS namespace_owners (
            user_id TEXT NOT NULL,
            namespace_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, namespace_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_namespace_owners_namespace ON namespace_owners(namespace_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth_clients (
            client_id TEXT PRIMARY KEY,
            namespace_id TEXT NOT NULL DEFAULT 'default',
            agent_id TEXT NOT NULL,
            client_info JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_oauth_clients_agent ON oauth_clients(agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_oauth_clients_ns_agent ON oauth_clients(namespace_id, agent_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth_auth_codes (
            code TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
            namespace_id TEXT NOT NULL DEFAULT 'default',
            code_challenge TEXT NOT NULL,
            redirect_uri TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            redirect_uri_provided_explicitly BOOLEAN NOT NULL DEFAULT TRUE,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_oauth_auth_codes_client_expires ON oauth_auth_codes(client_id, expires_at DESC)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth_access_tokens (
            access_token TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
            namespace_id TEXT NOT NULL DEFAULT 'default',
            agent_id TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'patchcord',
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_oauth_access_tokens_client_expires ON oauth_access_tokens(client_id, expires_at DESC)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
            refresh_token TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
            namespace_id TEXT NOT NULL DEFAULT 'default',
            agent_id TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'patchcord',
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_oauth_refresh_tokens_client_expires ON oauth_refresh_tokens(client_id, expires_at DESC)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS rate_limit_bans (
            token_hash TEXT PRIMARY KEY,
            banned_until TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_rate_limit_bans_until ON rate_limit_bans(banned_until)")

    for table in (
        "agent_messages", "agent_registry", "oauth_clients",
        "oauth_auth_codes", "oauth_access_tokens", "oauth_refresh_tokens",
        "rate_limit_bans", "bearer_tokens", "namespace_owners",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in (
        "oauth_refresh_tokens", "oauth_access_tokens", "oauth_auth_codes",
        "oauth_clients", "rate_limit_bans", "bearer_tokens",
        "namespace_owners", "agent_registry", "agent_messages",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
