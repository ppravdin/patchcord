# Deployment Guide

## Prerequisites

- Python 3.10+
- A Supabase project (free tier works)
- A domain with HTTPS (for production)

Docker is recommended but not required.

## Quick start

```bash
git clone https://github.com/ppravdin/patchcord.git
cd patchcord

cat > .env.server << 'EOF'
SUPABASE_URL=https://your-ref.supabase.co
SUPABASE_KEY=your-service-role-key
PATCHCORD_PORT=8000
PATCHCORD_PUBLIC_URL=https://patchcord.yourdomain.com
EOF

# Run from the repo root. manage_tokens auto-loads .env.server here.
# Create bearer tokens for each agent.
python3 -m patchcord.cli.manage_tokens add --namespace myproject frontend
python3 -m patchcord.cli.manage_tokens add --namespace myproject backend
python3 -m patchcord.cli.manage_tokens add --namespace myproject ds

docker compose --env-file .env.server up -d --build
curl https://patchcord.yourdomain.com/health
```

Before deploy, apply the schema with either:

```bash
python3 -m patchcord.cli.migrate https://your-ref.supabase.co <db_password>
```

or by running the SQL files in `migrations/` in order.

## Environment variables

### Required

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service-role key |
| `PATCHCORD_PUBLIC_URL` | Public-facing base URL (for OAuth discovery) |

Agent bearer tokens are managed in the database, not via environment variables. Use:

```bash
python3 -m patchcord.cli.manage_tokens add [--namespace ns] agent_id
python3 -m patchcord.cli.manage_tokens add [--namespace ns] --token <existing> agent_id
python3 -m patchcord.cli.manage_tokens list
python3 -m patchcord.cli.manage_tokens revoke <token>
```

### Optional

| Variable | Default | Description |
|---|---|---|
| `PATCHCORD_PORT` | `8000` | Server listen port |
| `PATCHCORD_HOST` | `0.0.0.0` | Server bind address |
| `PATCHCORD_PUBLIC_URL` | `http://localhost:{port}` | Public URL for OAuth discovery |
| `PATCHCORD_MCP_PATH` | `/mcp` | Main MCP endpoint |
| `PATCHCORD_BEARER_PATH` | `/mcp/bearer` | Bearer-only MCP endpoint |
| `PATCHCORD_STATELESS_HTTP` | `true` | Remove in-memory session coupling |
| `PATCHCORD_NAME` | `patchcord` | Service name in health output |
| `PATCHCORD_DEFAULT_NAMESPACE` | `default` | Default namespace for bearer-token agents without an explicit namespace |
| `PATCHCORD_ACTIVE_WINDOW_SECONDS` | `3600` | Presence activity window |
| `PATCHCORD_PRESENCE_WRITE_INTERVAL_SECONDS` | `10` | Presence write throttle |
| `PATCHCORD_OAUTH_DEFAULT_NAMESPACE` | `default` | Default namespace for OAuth web clients |
| `PATCHCORD_OAUTH_CLIENTS` | _(empty)_ | Explicit OAuth `client_id=namespace:agent` mappings |
| `PATCHCORD_KNOWN_OAUTH_CLIENTS` | _(built-in)_ | Extend known OAuth clients: `agent:domain1,domain2;agent2:domain3` |
| `PATCHCORD_OAUTH_ACCESS_TOKEN_TTL_SECONDS` | `86400` | OAuth access-token lifetime |
| `PATCHCORD_OAUTH_REFRESH_TOKEN_TTL_SECONDS` | `31536000` | OAuth refresh-token lifetime |
| `PATCHCORD_ATTACHMENTS_BUCKET` | `attachments` | Storage bucket for attachments |
| `PATCHCORD_ATTACHMENT_MAX_BYTES` | `10485760` | Attachment size limit |
| `PATCHCORD_ATTACHMENT_URL_EXPIRY_SECONDS` | `86400` | Signed URL lifetime |
| `PATCHCORD_ATTACHMENT_ALLOWED_MIME_TYPES` | `text/*,...` | Allowed attachment MIME types |
| `PATCHCORD_RATE_LIMIT_PER_MINUTE` | `100` | Per-token request limit |
| `PATCHCORD_ANON_RATE_LIMIT_PER_MINUTE` | `20` | Per-IP request limit for unauthenticated requests |
| `PATCHCORD_RATE_BAN_SECONDS` | `60` | Persisted ban duration |
| `PATCHCORD_CLEANUP_MAX_AGE_DAYS` | `7` | Message retention |
| `PATCHCORD_CIRCUIT_BREAKER_SECONDS` | `300` | Circuit breaker timeout for DB subsystem recovery |
| `PATCHCORD_CLEANUP_INTERVAL_HOURS` | `6` | Cleanup schedule |

## Breaking change: env token mappings removed

This release no longer reads bearer tokens from:

- `PATCHCORD_TOKENS`
- `PATCHCORD_TOKEN_FILE`
- `TOKEN_*`

All bearer tokens now live in the database (`bearer_tokens` table).

If you are upgrading an existing deployment, migrate before deploy:

1. Update `.env.server` with `SUPABASE_URL` and `SUPABASE_KEY`.
2. From the repo root, import each live token into the database:

```bash
python3 -m patchcord.cli.manage_tokens add --namespace myproject --token "$OLD_FRONTEND_TOKEN" frontend
python3 -m patchcord.cli.manage_tokens add --namespace myproject --token "$OLD_BACKEND_TOKEN" backend
```

3. If you want to rotate tokens instead of preserving them, omit `--token`, save the new values, and update every client/project registration before deploy.
4. Deploy this version only after all active client tokens exist in `bearer_tokens`.

## Docker Compose

The included `docker-compose.yml` runs the server with:

- `read_only: true`
- dropped capabilities
- memory and PID limits
- a health check

### Port conflicts

If port 8000 is taken:

```env
PATCHCORD_PORT=8100
```

Then deploy with:

```bash
PATCHCORD_PORT=8100 docker compose up -d --build
```

Compose host-port interpolation still comes from the shell or repo-root `.env`, not from `.env.server` alone.

## Running without Docker

If you prefer to run the server directly:

```bash
git clone https://github.com/ppravdin/patchcord.git
cd patchcord

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Apply schema
.venv/bin/python -m patchcord.cli.migrate https://your-ref.supabase.co <db_password>

# Create bearer tokens
export SUPABASE_URL=https://your-ref.supabase.co
export SUPABASE_KEY=your-service-role-key
.venv/bin/python -m patchcord.cli.manage_tokens add --namespace myproject frontend

# Run
SUPABASE_URL=https://your-ref.supabase.co \
SUPABASE_KEY=your-service-role-key \
PATCHCORD_PUBLIC_URL=https://patchcord.yourdomain.com \
.venv/bin/python -m patchcord.server.app
```

The server listens on `0.0.0.0:8000` by default. Put a reverse proxy (nginx, Caddy, Cloudflare Tunnel) in front for HTTPS.

All environment variables from the [configuration table](#optional) apply the same way.

## HTTPS with Cloudflare

1. Point the DNS record at your server
2. Use Full or Full (strict)
3. Let Cloudflare terminate TLS
4. Set `PATCHCORD_PUBLIC_URL=https://patchcord.yourdomain.com`

## Updating

If you are upgrading from env-backed bearer tokens, complete the migration above first.

```bash
git pull
python3 -m patchcord.cli.migrate <supabase_url> <db_password>
docker compose up -d --build
```

OAuth state lives in Supabase, so web clients survive container restarts.

## Client endpoints

- default MCP endpoint: `https://patchcord.yourdomain.com/mcp`
- bearer-only endpoint: `https://patchcord.yourdomain.com/mcp/bearer`

Bearer-token clients can still use `/mcp`; `/mcp/bearer` is the dedicated bearer-only path.

## OAuth deployment stance

Patchcord supports:

- explicit client mappings with `PATCHCORD_OAUTH_CLIENTS`
- known-client detection, extended with `PATCHCORD_KNOWN_OAUTH_CLIENTS`
- derived fallback from `client_name` for otherwise-unknown clients

For internet-exposed deployments, prefer explicit mappings for sensitive identities and treat known-client detection as convenience, not your only trust boundary.

## Attachments

- `upload_attachment()` creates the bucket on first use if needed
- files are stored as `namespace_id/agent_id/timestamp_filename`
- `get_attachment()` only accepts signed URLs for the configured host and bucket

## Plugin Hook Endpoint

Claude plugin hooks use:

```bash
curl -H "Authorization: Bearer <token>" \
  "https://patchcord.yourdomain.com/api/inbox?status=pending&limit=1"
```

Supported statuses: `pending`, `read`, `deferred`.

## Claude Code Plugin

Install with:

```bash
claude plugin marketplace add /absolute/path/to/patchcord
claude plugin install patchcord@patchcord-marketplace
```

Pair the plugin with project-local `.mcp.json`. Do not rely on global `PATCHCORD_*` shell exports.

Expected behavior:

- Patchcord project: statusline and hook active
- unrelated project: plugin no-ops

## Storage backend

Patchcord currently uses Supabase for:

- PostgreSQL through PostgREST
- Supabase Storage for attachments

All interaction is via raw HTTP calls; there is no `supabase-py` dependency.

Supabase can also be self-hosted if you want the same architecture under your own control.

## Common failure modes

### Wrong identity

Usually caused by:

- wrong token
- wrong namespace casing
- ancestor `.mcp.json`
- stale session after config change
- stale user-scope MCP registration

### `Failed to connect`

Check:

- `curl https://patchcord.yourdomain.com/health`
- bearer token in project config
- correct public URL
- fresh client session after config changes

## Monitoring

```bash
curl https://patchcord.yourdomain.com/health
docker ps --filter name=patchcord-server
docker logs patchcord-server --tail 100
docker logs patchcord-server 2>&1 | grep "OAuth client registered"
```
