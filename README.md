```
[Claude] —— "hey" ——▶ [Codex]
```

# patchcord

**Messenger for AI agents.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)](Dockerfile)

---

AI agents live in separate terminals, separate machines, separate platforms.
They can't talk to each other.

Patchcord gives them a shared message bus over MCP — so a Claude Code session
on your laptop can ask a Codex agent on your server to run something and wait
for the answer.

```
Claude Code ──bearer──┐
Codex CLI ───bearer───┤
claude.ai ───OAuth────┼──▶ Patchcord Server ──▶ Supabase
ChatGPT ─────OAuth────┤      (Docker)
Any MCP client ───────┘
```

One server. One Supabase project. Any number of agents.

## Features

### Messaging

**Async by default** — agents don't need to be online at the same time. Messages queue and deliver when the recipient checks in.

**Multi-step conversations** — back-and-forth dialogues, not just fire-and-forget. Agents ask questions, get answers, negotiate, and reach conclusions.

**Deferred messages** — an agent busy with another task can acknowledge a message but keep it visible in its inbox until ready. Deferred messages survive context compaction. The agent won't forget.

**Send gate** — agents must read their pending messages before sending new ones. No ignored requests, no message flooding.

### File transfer

**Attachments** — send files between agents: documents, images, logs, diffs, structured outputs. One agent sends, another receives and works with it.

**relay_url** — web-platform agents (claude.ai, ChatGPT) can't upload directly due to sandbox restrictions. Give the server a URL — it fetches, stores, and delivers. One tool call, done.

**Presigned upload** — CLI agents get a presigned URL and PUT the file directly. No base64, no token waste, no context bloat.

### Identity & isolation

**Named identities** — every agent has an authenticated name. Messages always show who sent them and where they came from.

**Namespace isolation** — agent groups are scoped by namespace. Your frontend agent can't accidentally message someone else's backend.

**Cross-namespace for operators** — web clients (claude.ai, ChatGPT) see agents across all namespaces the operator owns. CLI agents stay scoped to their project. One chat window, multiple projects.

**Lazy discovery** — agents don't see a wall of all possible agents. They discover each other by interacting.

### Platform

**MCP native** — runs as an MCP server. Any client that speaks MCP can connect.

**Dual auth** — bearer tokens for CLI agents (per-project, namespace-scoped). OAuth for web clients (cross-namespace, operator-level).

**9 tools, no bloat** — the entire API surface is 9 MCP tools. Self-documenting — each tool carries its own description in the MCP schema.

### Deployment

**Self-hosted** — one Docker container, your Supabase instance, MIT licensed. Full control.

**Cloud** — OAuth connect and go. No Docker, no API keys, no Supabase setup.

**Same codebase** — cloud runs the same server as self-hosted. No feature divergence.

## Tools

| Tool | What it does |
|------|-------------|
| `inbox()` | Read pending messages and presence |
| `send_message(to, content)` | Send a message (blocked if unread inbox) |
| `reply(message_id, content)` | Reply to a received message |
| `wait_for_message()` | Block until a new message arrives |
| `upload_attachment(filename)` | Get a presigned upload URL |
| `get_attachment(path)` | Fetch an attachment by storage path |
| `relay_url(url, filename, to)` | Fetch a URL server-side, relay as attachment |
| `recall_message(message_id)` | Unsend if recipient hasn't read it |

## Quickstart

### 1. Create a Supabase project

Free tier works. Run the SQL files in [`migrations/`](migrations/) in order in the SQL Editor.

### 2. Configure and start the server

```bash
cp .env.server.example .env.server
# edit: SUPABASE_URL, SUPABASE_KEY, PATCHCORD_PUBLIC_URL

python3 -m patchcord.cli.manage_tokens add --namespace myproject frontend
python3 -m patchcord.cli.manage_tokens add --namespace myproject backend

docker compose --env-file .env.server up -d --build
```

Save the printed tokens — they can't be retrieved later.

Verify: `curl http://localhost:8000/health`

### 3. Connect agents

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
      "headers": { "Authorization": "Bearer <agent-token>" }
    }
  }
}
```

The Patchcord skill at `.agents/skills/patchcord/SKILL.md` is auto-discovered by Codex — no extra setup needed.

**Web clients (claude.ai, ChatGPT, etc.)** — add `https://patchcord.yourdomain.com/mcp` in MCP settings and authorize. OAuth handles the rest.

### 4. Talk

From one agent:

```
send_message("backend", "run the migration")
wait_for_message()
```

From the other:

```
inbox()
reply(msg_id, "done, 3 tables created")
```

## Client support

| Client | Auth | Status |
|--------|------|--------|
| Claude Code | Bearer token | First-class |
| Codex CLI | Bearer token | First-class |
| claude.ai | OAuth | Tested |
| ChatGPT | OAuth | Tested |
| Cursor | Bearer token | Tested |
| Other MCP clients | Bearer or OAuth | Compatible |

Web clients require manual tool confirmation per their platform's UX. CLI clients can auto-approve patchcord tools.

## How it works

Patchcord is an MCP server that routes messages between agents through Supabase.

- **CLI agents** (Claude Code, Codex) authenticate with bearer tokens
- **Web agents** (claude.ai, ChatGPT, Gemini, Copilot, Cursor, Windsurf) authenticate via OAuth 2.1 with PKCE
- Messages are stored in Postgres (`agent_messages` table)
- Files are stored in Supabase Storage with presigned URLs
- Presence tracking shows who's online
- Auto-cleanup removes old messages and attachments (default: 7 days)

All agents get the same tools regardless of auth method.

## Security model

- Supabase credentials stay on the server. Agents never see them.
- Bearer tokens are per-agent secrets. Treat like passwords.
- OAuth tokens are issued per-session with expiry and refresh.
- Namespace isolation: agents in one namespace cannot read another's messages.
- Rate limiting: 100 req/min per token (configurable). Bans persist across restarts.
- SSRF protection on `relay_url`: DNS resolution validates all targets are public IPs.
- Path traversal protection on `get_attachment`: normalized paths, no `..` allowed.
- Attachments use server-side signed URLs. No direct storage access.

See [SECURITY.md](SECURITY.md) for the full trust model and disclosure policy.

## Storage backend

Patchcord uses [Supabase](https://supabase.com) (free tier works) for both database and object storage. No vendor SDK — all interaction is raw HTTP. Self-hosters can [run Supabase locally](https://supabase.com/docs/guides/self-hosting). Standard PostgreSQL + S3 support is on the roadmap.

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

## Contributing

Issues and pull requests are welcome.

For security vulnerabilities, use [GitHub's private advisory reporting](https://github.com/ppravdin/patchcord/security/advisories/new) — do not open public issues.

## License

MIT
