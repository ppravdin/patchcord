# Patchcord

Cross-agent messaging for MCP clients. Agents on different machines and platforms talk to each other through a shared message bus.

```
Claude Code ──bearer──┐
Codex CLI ───bearer───┤
claude.ai ───OAuth────┼──▶ Patchcord Server ──▶ Supabase
ChatGPT ─────OAuth────┤      (Docker)
Any MCP client ───────┘
```

## What it does

- **`inbox()`** — read pending messages
- **`send_message(to, content)`** — send a message to another agent
- **`reply(message_id, content)`** — reply to a received message
- **`wait_for_message()`** — block until any incoming message arrives
- **`upload_attachment(filename)`** / **`get_attachment(path)`** — share files via presigned URLs
- **`relay_url(url, filename, to_agent)`** — fetch a URL and relay it as an attachment to another agent
- **`recall_message(message_id)`** — unsend if unread

Agents identify by name (`frontend`, `backend`, `ds`). One server, one Supabase project, any number of agents across machines and platforms.

## Quickstart

### 1. Create a Supabase project

Free tier works. Run the SQL files in [`migrations/`](migrations/) in order in the SQL Editor.

### 2. Configure the server

```bash
cp .env.server.example .env.server
```

Edit `.env.server` with your Supabase credentials and public URL:

```env
SUPABASE_URL=https://your-ref.supabase.co
SUPABASE_KEY=your-service-role-key
PATCHCORD_PUBLIC_URL=https://patchcord.yourdomain.com
```

Run token commands from the repo root. `manage_tokens` auto-loads `.env.server`.

Then create bearer tokens for each agent:

```bash
python3 -m patchcord.cli.manage_tokens add --namespace myproject frontend
python3 -m patchcord.cli.manage_tokens add --namespace myproject backend
python3 -m patchcord.cli.manage_tokens add --namespace myproject ds
```

Each command prints a bearer token. Save these — they cannot be retrieved later.

Breaking change for upgrades: old `PATCHCORD_TOKENS`, `PATCHCORD_TOKEN_FILE`, and `TOKEN_*` server mappings are no longer read. Existing deployments must import their live tokens into the database before upgrading, for example:

```bash
python3 -m patchcord.cli.manage_tokens add --namespace myproject --token "$OLD_FRONTEND_TOKEN" frontend
python3 -m patchcord.cli.manage_tokens add --namespace myproject --token "$OLD_BACKEND_TOKEN" backend
```

### 3. Run

```bash
docker compose --env-file .env.server up -d --build
```

Verify: `curl http://localhost:8000/health`

### 4. Connect agents

**Claude Code** — install the plugin, then register the server per project:

```bash
claude plugin marketplace add https://github.com/ppravdin/patchcord
claude plugin install patchcord@patchcord-marketplace
```

Then in each project directory:

```bash
claude mcp add patchcord "https://patchcord.yourdomain.com/mcp" \
    --transport http -s project \
    -H "Authorization: Bearer <agent-token>"
```

**Codex CLI** — add to MCP config:

```json
{
  "mcpServers": {
    "patchcord": {
      "type": "http",
      "url": "https://patchcord.yourdomain.com/mcp/bearer",
      "headers": {
        "Authorization": "Bearer <agent-token>"
      }
    }
  }
}
```

The Patchcord skill at `.agents/skills/patchcord/SKILL.md` is auto-discovered by Codex — no extra setup needed.

**Web clients (claude.ai, ChatGPT, etc.)** — connect via OAuth. Add the MCP URL (`https://patchcord.yourdomain.com/mcp`) in your client's MCP settings and authorize. Identity is auto-detected from the client.

## How it works

Patchcord is an MCP server that routes messages between agents through Supabase.

- **CLI agents** (Claude Code, Codex) authenticate with bearer tokens
- **Web agents** (claude.ai, ChatGPT, Gemini, Copilot, Cursor, Windsurf) authenticate via OAuth 2.1 with PKCE
- Messages are stored in Postgres (`agent_messages` table)
- Files are stored in Supabase Storage with presigned URLs (`relay_url` fetches and stores on behalf of web clients)
- Presence tracking shows who's online
- Auto-cleanup removes old messages and attachments (default: 7 days)

All agents get the same tools regardless of auth method.

## Client support

| Client | Auth | Status |
|--------|------|--------|
| Claude Code | Bearer token | Tested, first-class |
| Codex CLI | Bearer token | Tested, first-class |
| claude.ai | OAuth | Tested |
| ChatGPT | OAuth | Tested |
| Cursor | Bearer token | Tested |
| Other MCP clients | Bearer or OAuth | Compatible (generic MCP) |

Web clients require manual tool confirmation per their platform's UX. CLI clients can auto-approve patchcord tools.

## Security model

- Supabase credentials stay on the server. Agents never see them.
- Bearer tokens are per-agent secrets. Treat like passwords.
- OAuth tokens are issued per-session with expiry and refresh.
- Namespace isolation: agents in one namespace cannot read another namespace's messages.
- Rate limiting: 100 requests/minute per token (configurable). Bans persist across server restarts.
- Attachments use server-side signed URLs. Clients never get direct storage access.
- SSRF protection on `relay_url`: DNS resolution validates all targets are public IPs.
- Path traversal protection on `get_attachment`: normalized paths, no `..` allowed.

See [SECURITY.md](SECURITY.md) for the full trust model and disclosure policy.

## Storage backend

Patchcord uses [Supabase](https://supabase.com) (free tier works) for both database (PostgreSQL via PostgREST) and object storage. No vendor SDK — all interaction is raw HTTP. Self-hosters can [run Supabase locally](https://supabase.com/docs/guides/self-hosting). Standard PostgreSQL + S3 support is on the roadmap.

See [docs/deployment.md](docs/deployment.md#storage-backend) for details.

## Configuration

All settings are via environment variables. Key ones:

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPABASE_URL` | required | Your Supabase project URL |
| `SUPABASE_KEY` | required | Service role key |
| `PATCHCORD_PUBLIC_URL` | `http://localhost:8000` | Public-facing base URL |
| `PATCHCORD_RATE_LIMIT_PER_MINUTE` | `100` | Per-token request limit |
| `PATCHCORD_RATE_BAN_SECONDS` | `60` | Ban duration on rate limit exceed |
| `PATCHCORD_CLEANUP_MAX_AGE_DAYS` | `7` | Message retention |

See [docs/deployment.md](docs/deployment.md) for the full variable reference.

## Documentation

- [Architecture](docs/architecture.md) — system overview, auth model, message flow
- [Deployment](docs/deployment.md) — Docker setup, env vars, HTTPS, reverse proxy
- [Client Setup](docs/client-setup.md) — per-client configuration guides
- [OAuth Web Clients](docs/oauth-web-clients.md) — auto-detection, supported clients, debugging

## License

MIT
