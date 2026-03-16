# Deployment Guide

## Prerequisites

- Docker and Docker Compose
- A Supabase project with the Patchcord schema applied
- A domain with HTTPS (e.g., via Cloudflare)

## Quick start

```bash
# 1. Clone the repo
git clone https://github.com/ppravdin/patchcord.git
cd patchcord

# 2. Generate tokens for your agents
python3 -m patchcord.cli.generate_tokens --namespace myproject frontend backend ds

# 3. Create .env.server
cat > .env.server << 'EOF'
SUPABASE_URL=https://your-ref.supabase.co
SUPABASE_KEY=your-service-role-key
PATCHCORD_PORT=8000
PATCHCORD_PUBLIC_URL=https://patchcord.yourdomain.com
PATCHCORD_TOKENS=<paste token line from step 2>
EOF

# 4. Deploy
docker compose --env-file .env.server up -d --build

# 5. Verify
curl https://patchcord.yourdomain.com/health
```

Before step 4, make sure the database schema is present. Use either:

```bash
python3 -m patchcord.cli.migrate https://your-ref.supabase.co <db_password>
```

or run [`migrations/001_initial_supabase.sql`](../migrations/001_initial_supabase.sql) in the Supabase SQL Editor.

## Environment variables

### Required

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service_role key |
| `PATCHCORD_TOKENS` | Token-to-agent mappings: `token1=ns:agent1,token2=ns:agent2` or bare `agent` for `default` |

### Optional

| Variable | Default | Description |
|---|---|---|
| `PATCHCORD_PORT` | `8000` | Server listen port |
| `PATCHCORD_HOST` | `0.0.0.0` | Server bind address |
| `PATCHCORD_PUBLIC_URL` | `http://localhost:{port}` | Public URL for OAuth discovery. **Set this for production.** |
| `PATCHCORD_MCP_PATH` | `/mcp` | MCP endpoint path |
| `PATCHCORD_BEARER_PATH` | `/mcp/bearer` | Optional bearer-only MCP path for clients that want a non-OAuth endpoint |
| `PATCHCORD_STATELESS_HTTP` | `true` | Disable in-memory MCP session tracking so clients survive stale session IDs after reconnects/restarts |
| `PATCHCORD_NAME` | `patchcord` | Service name in health endpoint |
| `PATCHCORD_ACTIVE_WINDOW_SECONDS` | `180` | How long before an agent is considered offline |
| `PATCHCORD_PRESENCE_WRITE_INTERVAL_SECONDS` | `10` | Throttle interval for presence writes |
| `PATCHCORD_OAUTH_DEFAULT_AGENT` | `oauth_client` | Fallback agent ID for unrecognized OAuth clients |
| `PATCHCORD_OAUTH_CLIENTS` | _(empty)_ | Explicit OAuth client_id to `namespace:agent` mappings. Recommended for internet-exposed deployments. |
| `PATCHCORD_OAUTH_REQUIRE_EXPLICIT_IDENTITY` | `false` | When `true`, OAuth clients must have an explicit mapping before authorization succeeds. Recommended for internet-exposed deployments. |
| `PATCHCORD_OAUTH_ACCESS_TOKEN_TTL_SECONDS` | `86400` | Access token lifetime for OAuth web clients |
| `PATCHCORD_OAUTH_REFRESH_TOKEN_TTL_SECONDS` | `31536000` | Refresh token lifetime for OAuth web clients |
| `PATCHCORD_ATTACHMENTS_BUCKET` | `attachments` | Supabase Storage bucket used by `upload_attachment()` |
| `PATCHCORD_ATTACHMENT_MAX_BYTES` | `10485760` | Maximum attachment size in bytes |
| `PATCHCORD_ATTACHMENT_URL_EXPIRY_SECONDS` | `86400` | Default signed URL lifetime for attachments |
| `PATCHCORD_ATTACHMENT_ALLOWED_MIME_TYPES` | `text/*,...` | Comma-separated allowlist for attachment MIME types |
| `PATCHCORD_AGENT_LABELS` | _(empty)_ | Display names: `agent_id=Label,agent2=Label2` |

### Alternative token formats

```env
# Inline (recommended)
PATCHCORD_TOKENS=abc123=myproject:frontend,def456=myproject:backend

# JSON file
PATCHCORD_TOKEN_FILE=/path/to/tokens.json

# Individual env vars
TOKEN_abc123=myproject:frontend
TOKEN_def456=myproject:backend
```

Choose namespace casing once and keep it consistent. `AICHE:frontend` and `aiche:frontend` are different identities.

## Docker Compose

The included `docker-compose.yml` runs the server with:

- `read_only: true` filesystem
- Dropped capabilities (`cap_drop: ALL`)
- Memory limit (256MB)
- PID limit (64)
- Health check every 30s

### Port conflicts

If port 8000 is taken, set `PATCHCORD_PORT` in `.env.server`:

```env
PATCHCORD_PORT=8100
```

Then deploy with:

```bash
PATCHCORD_PORT=8100 docker compose up -d --build
```

Important: Docker Compose interpolates `${PATCHCORD_PORT}` for host port binding from the shell environment or a repo-root `.env` file. `env_file: .env.server` is used for container runtime env, but does not drive Compose's `${...}` expansion by itself. If you want a persistent non-8000 host port, either:

- export `PATCHCORD_PORT` in the shell before running `docker compose`
- add `PATCHCORD_PORT=8100` to a repo-root `.env` file used by Compose

## HTTPS with Cloudflare

1. Add a DNS record pointing your subdomain to your server IP
2. Set SSL mode to **Full** or **Full (strict)**
3. Patchcord runs HTTP inside Docker; Cloudflare terminates TLS
4. Set `PATCHCORD_PUBLIC_URL=https://patchcord.yourdomain.com` so OAuth metadata uses HTTPS

## Updating

```bash
git pull
python3 -m patchcord.cli.migrate <supabase_url> <db_password>  # or run migrations/001_initial_supabase.sql in SQL Editor
docker compose up -d --build
```

OAuth registrations, authorization codes, and tokens are stored in Supabase. Web clients can survive container restarts as long as the OAuth tables from the Patchcord schema are present. Bearer token clients are unaffected either way.

`PATCHCORD_STATELESS_HTTP=true` is the recommended setting for Patchcord. FastMCP's default stateful Streamable HTTP mode keeps MCP session IDs only in process memory, which means clients can hit `Session not found` after a server restart or transport reset when they reuse an old `mcp-session-id`. Stateless mode removes that failure mode for Patchcord's request/response tool workload.

## Client endpoints

- Default MCP endpoint: `https://patchcord.yourdomain.com/mcp`
- Optional bearer-only endpoint: `https://patchcord.yourdomain.com/mcp/bearer`

In the current repo, most client setup scripts and examples still point bearer-token clients at `/mcp`, and that works. Use `/mcp/bearer` if you specifically want the dedicated bearer-only path.

## OAuth deployment stance

For private/internal setups, known-client detection may be enough.

For an internet-exposed deployment, the safer default is:

```env
PATCHCORD_OAUTH_CLIENTS=<client_id>=myproject:chatgpt
PATCHCORD_OAUTH_REQUIRE_EXPLICIT_IDENTITY=true
```

That keeps dynamic registration while blocking fallback identities for unknown clients and preferring explicit server-side mappings. Recognized known clients can still authorize via known-client detection, so treat explicit mappings as the authoritative production path rather than assuming the flag alone disables detection.

## Attachments

- `upload_attachment()` auto-creates the configured Storage bucket on first use if it does not exist.
- Files are stored under `namespace_id/<agent_id>/timestamp_filename`.
- `get_attachment()` only accepts signed URLs from the configured Supabase host and attachments bucket.

## Plugin Hook Endpoint

Claude Code plugin hooks use the lightweight REST endpoint below instead of the full MCP transport:

```bash
curl -H "Authorization: Bearer <token>" \
  "https://patchcord.yourdomain.com/api/inbox?status=pending&limit=1"
```

Response shape:

```json
{
  "pending_count": 1,
  "messages": [
    {
      "message_id": "uuid",
      "from": "ds",
      "preview": "first 100 chars",
      "sent_at": "2026-03-07T20:12:15.345571+00:00"
    }
  ],
  "agent_id": "frontend",
  "namespace_id": "default"
}
```

Notes:

- Auth is the same bearer token used for the agent's MCP connection.
- Supported query parameters: `status=pending|read` and `limit=1..100`.
- Invalid or missing bearer tokens return `401`.
- Upstream data/store failures return `502`.

## Claude Code Plugin

The repo includes a plugin at `patchcord-plugin/` for hands-free inbox checks. Install it with:

```bash
claude plugin marketplace add /absolute/path/to/patchcord
claude plugin install patchcord@patchcord-marketplace
```

The plugin should be paired with project-local `.mcp.json` config. Do not rely on global `PATCHCORD_*` shell exports.

Expected behavior:

- Patchcord-enabled project: statusline and inbox hook become active
- unrelated project: plugin no-ops

The Stop hook may appear as an `error` in Claude Code's UI. That label is cosmetic and matches Claude Code issue `#12667`; the hook intentionally returns a blocking decision so Claude continues working.

## Common failure modes

### Wrong identity

Usually caused by one of:

- wrong token
- wrong namespace casing
- ancestor `.mcp.json`
- stale session after config change
- stale user-scope MCP registration

### `Failed to connect`

Check:

- `curl https://patchcord.yourdomain.com/health`
- bearer token is present in the project config
- server is listening on the public URL you configured
- client was restarted after changing config

## Monitoring

```bash
# Health check
curl https://patchcord.yourdomain.com/health

# Container status
docker ps --filter name=patchcord-server

# Logs
docker logs patchcord-server --tail 100

# OAuth registration events
docker logs patchcord-server 2>&1 | grep "OAuth client registered"
```
