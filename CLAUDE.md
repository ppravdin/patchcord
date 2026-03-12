You have patchcord installed. Call inbox() once at session start to check for pending messages.

# Patchcord

Cross-agent messaging MCP server. Agents on different machines talk through a shared message bus backed by Supabase.

## Structure

- `patchcord/server/` — centralized MCP server (Docker, FastMCP, OAuth, rate limiting)
  - `app.py` — Starlette app, middleware, REST endpoints
  - `tools.py` — all MCP tool implementations
  - `oauth.py` — OAuth 2.1 provider with auto-detection
  - `config.py` — env var loading, token parsing, known clients
  - `helpers.py` — presence, circuit breakers, storage helpers
- `patchcord/core/` — shared code (formatting, attachments, instructions)
- `patchcord/direct/` — legacy direct-to-Supabase mode
- `patchcord/cli/` — CLI tools (migrate, manage_tokens)
- `patchcord-plugin/` — Claude Code plugin (inbox hook)
- `migrations/` — SQL schema files

## Commands

```bash
# Run server locally
docker compose --env-file .env.server up -d --build

# Health check
curl http://localhost:8000/health

# Run migrations
python3 -m patchcord.cli.migrate <supabase_url> <db_password>

# Manage bearer tokens
python3 -m patchcord.cli.manage_tokens add [--namespace ns] agent_id
python3 -m patchcord.cli.manage_tokens list
python3 -m patchcord.cli.manage_tokens revoke <token>

# Lint
ruff check patchcord/
ruff format --check patchcord/
```

## Key patterns

- All Supabase access is via PostgREST HTTP (httpx), no vendor SDK
- Auth: bearer tokens (CLI) + OAuth 2.1 with PKCE (web clients)
- Transient failures use timed circuit breakers (5 min), not permanent disable flags
- Rate limit bans persist to DB (SHA-256 hashed tokens)
