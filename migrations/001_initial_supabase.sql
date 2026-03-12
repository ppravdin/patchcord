-- Run this in Supabase SQL Editor (once)

create table if not exists agent_messages (
  id uuid default gen_random_uuid() primary key,
  from_agent text not null,
  to_agent text not null,
  content text not null,
  reply_to uuid references agent_messages(id),
  status text default 'pending' check (status in ('pending', 'read', 'replied')),
  created_at timestamptz default now()
);

-- Additive migration for topic field
alter table agent_messages add column if not exists topic text;

-- Namespace isolation: add namespace_id to messages
alter table agent_messages add column if not exists namespace_id text not null default 'default';

-- Indexes for fast message queries (namespace-aware)
create index if not exists idx_agent_messages_ns_to_status on agent_messages(namespace_id, to_agent, status);
create index if not exists idx_agent_messages_ns_reply_to on agent_messages(namespace_id, reply_to);
create index if not exists idx_agent_messages_topic on agent_messages(topic) where topic is not null;

-- Online agent registry (presence + machine identity)
create table if not exists agent_registry (
  agent_id text primary key,
  display_name text,
  machine_name text not null,
  status text not null default 'online' check (status in ('online', 'offline')),
  last_seen timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  meta jsonb not null default '{}'::jsonb
);

-- Additive migration for older installs that already created agent_registry with fewer fields
alter table agent_registry add column if not exists display_name text;
alter table agent_registry add column if not exists machine_name text;
alter table agent_registry add column if not exists status text;
alter table agent_registry add column if not exists last_seen timestamptz;
alter table agent_registry add column if not exists updated_at timestamptz;
alter table agent_registry add column if not exists meta jsonb;

-- Backfill defaults for nullable rows (if table existed before these defaults)
update agent_registry set status = coalesce(status, 'online');
update agent_registry set machine_name = coalesce(machine_name, 'unknown');
update agent_registry set last_seen = coalesce(last_seen, now());
update agent_registry set updated_at = coalesce(updated_at, now());
update agent_registry set meta = coalesce(meta, '{}'::jsonb);

-- Enforce not-null and defaults after backfill
alter table agent_registry alter column status set default 'online';
alter table agent_registry alter column machine_name set default 'unknown';
alter table agent_registry alter column last_seen set default now();
alter table agent_registry alter column updated_at set default now();
alter table agent_registry alter column meta set default '{}'::jsonb;

alter table agent_registry alter column status set not null;
alter table agent_registry alter column machine_name set not null;
alter table agent_registry alter column last_seen set not null;
alter table agent_registry alter column updated_at set not null;
alter table agent_registry alter column meta set not null;

-- Namespace isolation: add namespace_id to registry and migrate PK
alter table agent_registry add column if not exists namespace_id text not null default 'default';

-- Migrate from single-column PK to composite PK (namespace_id, agent_id).
-- Safety: deduplicate first, then swap PK.
-- This block is idempotent — safe to re-run.
do $$
begin
  -- Check if PK is still single-column (agent_id only)
  if exists (
    select 1 from information_schema.table_constraints tc
    join information_schema.key_column_usage kcu on tc.constraint_name = kcu.constraint_name
    where tc.table_name = 'agent_registry'
      and tc.constraint_type = 'PRIMARY KEY'
    group by tc.constraint_name
    having count(*) = 1
    and bool_or(kcu.column_name = 'agent_id')
  ) then
    alter table agent_registry drop constraint agent_registry_pkey;
    alter table agent_registry add primary key (namespace_id, agent_id);
  end if;
end $$;

create index if not exists idx_agent_registry_ns_status_seen on agent_registry(namespace_id, status, last_seen desc);
create index if not exists idx_agent_registry_seen on agent_registry(last_seen desc);

-- Durable OAuth storage for web MCP clients (ChatGPT, claude.ai, etc.)
create table if not exists oauth_clients (
  client_id text primary key,
  namespace_id text not null default 'default',
  agent_id text not null,
  client_info jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table oauth_clients add column if not exists namespace_id text not null default 'default';

create table if not exists oauth_auth_codes (
  code text primary key,
  client_id text not null references oauth_clients(client_id) on delete cascade,
  namespace_id text not null default 'default',
  code_challenge text not null,
  redirect_uri text not null,
  agent_id text not null,
  redirect_uri_provided_explicitly boolean not null default true,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

alter table oauth_auth_codes add column if not exists namespace_id text not null default 'default';

create table if not exists oauth_access_tokens (
  access_token text primary key,
  client_id text not null references oauth_clients(client_id) on delete cascade,
  namespace_id text not null default 'default',
  agent_id text not null,
  scope text not null default 'patchcord',
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

alter table oauth_access_tokens add column if not exists namespace_id text not null default 'default';

create table if not exists oauth_refresh_tokens (
  refresh_token text primary key,
  client_id text not null references oauth_clients(client_id) on delete cascade,
  namespace_id text not null default 'default',
  agent_id text not null,
  scope text not null default 'patchcord',
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

alter table oauth_refresh_tokens add column if not exists namespace_id text not null default 'default';

create index if not exists idx_oauth_clients_agent on oauth_clients(agent_id);
create index if not exists idx_oauth_clients_ns_agent on oauth_clients(namespace_id, agent_id);
create index if not exists idx_oauth_auth_codes_client_expires on oauth_auth_codes(client_id, expires_at desc);
create index if not exists idx_oauth_access_tokens_client_expires on oauth_access_tokens(client_id, expires_at desc);
create index if not exists idx_oauth_refresh_tokens_client_expires on oauth_refresh_tokens(client_id, expires_at desc);

-- Deferred messages: add 'deferred' to agent_messages status constraint.
-- A deferred message has been seen by the agent (they replied with defer=true)
-- but stays in their inbox until they reply again without defer.
do $$
begin
  -- Only alter if the constraint doesn't already include 'deferred'
  if not exists (
    select 1 from information_schema.check_constraints
    where constraint_name = 'agent_messages_status_check'
      and check_clause like '%deferred%'
  ) then
    alter table agent_messages drop constraint if exists agent_messages_status_check;
    alter table agent_messages add constraint agent_messages_status_check
      check (status in ('pending', 'read', 'replied', 'deferred'));
  end if;
end $$;

-- Enable RLS (optional, skip for personal use)
-- alter table agent_messages enable row level security;
-- alter table agent_registry enable row level security;
-- alter table oauth_clients enable row level security;
-- alter table oauth_auth_codes enable row level security;
-- alter table oauth_access_tokens enable row level security;
-- alter table oauth_refresh_tokens enable row level security;
