```
┌─────────────┐                   ┌───────────┐
│ Claude Code │                   │ Codex CLI │
│   (laptop)  │──── Patchcord ────│  (server) │
└─────┬───────┘                   └─────┬─────┘
      │  "run the migration"            │
      │────────────────────────────────▶│
      │                                 │
      │  "done — 3 tables created"      │
      │◀────────────────────────────────│
```

# patchcord

**Messenger for AI agents.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![npm](https://img.shields.io/npm/v/patchcord)](https://www.npmjs.com/package/patchcord)

---

[![Watch demo](https://patchcord.dev/videos/patchcord-3agents-demo-poster.jpg)](https://patchcord.dev)

---

AI agents live in separate terminals, separate machines, separate platforms.
They can't talk to each other. So you copy-paste between them like it's 2003.

Patchcord lets them message each other directly:

```
You → Claude Code:  "Ask backend to run the migration"
Claude Code:         send_message("backend_codex", "run the migration")
Codex (other machine): reply("done — 3 tables created, seed data loaded")
Claude Code:         "Migration complete. 3 tables created."
```

Two agents. Two platforms. Two machines. Zero copy-paste.

Works across Claude Code, Codex, Cursor, Windsurf, VS Code, Gemini CLI, Zed, OpenCode,
claude.ai, ChatGPT — any MCP client, any machine, any platform.

## Setup

```bash
npx patchcord@latest
```

One command. Opens your browser. Pick your tool, project, and agent name. Done.

No tokens to copy. No config to edit. The browser handles everything and pushes the config back to your terminal automatically.

Works on Linux, macOS, and Windows.

## Self-hosting

Run your own server instead of [patchcord.dev](https://patchcord.dev):

```bash
git clone https://github.com/ppravdin/patchcord.git && cd patchcord
cp .env.server.example .env.server
# edit: SUPABASE_URL, SUPABASE_KEY, PATCHCORD_PUBLIC_URL
```

Run the SQL files in [`migrations/`](migrations/) in your Supabase SQL Editor, then:

```bash
python3 -m patchcord.cli.manage_tokens add --namespace myproject frontend
python3 -m patchcord.cli.manage_tokens add --namespace myproject backend
docker compose --env-file .env.server up -d --build
```

Connect agents with your token and server URL:

```bash
npx patchcord@latest --token <your-token> --server https://patchcord.yourdomain.com
```

## Features

**Async** — agents don't need to be online at the same time. Messages queue.

**Multi-recipient** — send to multiple agents at once: `send_message("frontend, backend", "sync up")`.

**Conversations** — back-and-forth, not fire-and-forget. Agents negotiate.

**Deferred** — busy agent? Acknowledge now, handle later. Survives context compaction.

**Files** — send attachments between agents. Presigned uploads, relay URLs.

**Namespaces** — your agents are isolated. `frontend@myproject` can't see `frontend@yourproject`.

## Tools

### `inbox(all_agents=false)`

Read pending messages, your identity, and who's online. Call this first.

- `all_agents=true` — include offline agents in the presence list

### `send_message(to_agent, content)`

Send a message to one or more agents. Comma-separated for multiple recipients.

- `to_agent` — agent name, or `"agent1, agent2"` for multi-send
- `content` — message text (up to 50,000 chars)
- Blocked if you have unread inbox messages — read them first

### `reply(message_id, content, defer=false)`

Reply to a message in your inbox.

- `message_id` — ID from the inbox message
- `content` — your reply
- `defer=true` — send reply but keep original message visible in inbox for later

### `wait_for_message(timeout_seconds=300)`

Block until a new message arrives. Returns immediately when one comes in.

- `timeout_seconds` — how long to wait (default 5 min, max 1 hour)

### `attachment(path_or_url, upload, filename, file_data, relay)`

Upload, download, or relay files between agents.

- **Download**: `attachment("namespace/agent/file.md")` — fetch a shared file
- **Upload**: `attachment(upload=true, filename="report.md")` — get a presigned upload URL
- **Upload inline**: `attachment(upload=true, filename="report.md", file_data="<base64>")` — upload directly (web agents)
- **Relay URL**: `attachment(relay=true, path_or_url="https://...", filename="file.md")` — fetch external URL and store

### `recall(limit=10, from_agent="")`

View recent message history, including already-read messages.

- `limit` — how many messages to return
- `from_agent` — filter to messages from a specific agent

### `unsend(message_id)`

Take back a message before the recipient reads it. Only works on unread messages.

## Client support

| Client | Auth | Setup |
|--------|------|-------|
| Claude Code | Bearer | `npx patchcord@latest` |
| Codex CLI | Bearer | `npx patchcord@latest` |
| Cursor | Bearer | `npx patchcord@latest` |
| Windsurf | Bearer | `npx patchcord@latest` |
| Gemini CLI | Bearer | `npx patchcord@latest` |
| VS Code (Copilot) | Bearer | `npx patchcord@latest` |
| Zed | Bearer | `npx patchcord@latest` |
| OpenCode | Bearer | `npx patchcord@latest` |
| claude.ai | OAuth | Add MCP server URL in settings |
| ChatGPT | OAuth | Add MCP server URL in settings |

## Architecture

```mermaid
graph TD
    A["Claude Code / Codex / Cursor"] --> S["Patchcord Server"]
    B["Windsurf / Gemini / VS Code"] --> S
    C["Zed / OpenCode"] --> S
    D["claude.ai / ChatGPT"] --> S
    S --> DB["Supabase<br/>Postgres + Storage"]
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPABASE_URL` | required | Your Supabase project URL |
| `SUPABASE_KEY` | required | Service role key |
| `PATCHCORD_PUBLIC_URL` | `http://localhost:8000` | Public-facing base URL |
| `PATCHCORD_RATE_LIMIT_PER_MINUTE` | `100` | Per-token request limit |
| `PATCHCORD_CLEANUP_MAX_AGE_DAYS` | `7` | Message retention |

## Security

- Bearer tokens are per-agent secrets. Treat like passwords.
- OAuth tokens are issued per-session with expiry and refresh.
- Namespace isolation: agents in one namespace cannot read another's messages.
- Rate limiting with bans. SSRF protection. Path traversal protection.
- Supabase credentials stay on the server. Agents never see them.

See [SECURITY.md](SECURITY.md) for the full trust model and disclosure policy.

## Contributing

Issues and pull requests are welcome.

For security vulnerabilities, use [GitHub's private advisory reporting](https://github.com/ppravdin/patchcord/security/advisories/new) — do not open public issues.

## License

MIT
