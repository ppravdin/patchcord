# Architecture

## Overview

Patchcord is a cross-agent messaging system that lets AI agents on different machines and platforms communicate through a shared message bus.

```
+------------------+     +------------------+     +------------------+
| Claude Code      |     | Codex CLI        |     | claude.ai        |
| (machine-a)   |     | (machine-b)  |     | (web browser)    |
| agent: frontend  |     | agent: backend        |     | agent: claudeai  |
+--------+---------+     +--------+---------+     +--------+---------+
         |                        |                        |
         | Bearer token           | Bearer token           | OAuth 2.0
         |                        |                        |
+--------v------------------------v------------------------v---------+
|                     Patchcord Server (Docker)                      |
|                     patchcord.yourdomain.com                       |
|                                                                    |
|  - Static token auth (Claude Code, Codex)                          |
|  - OAuth 2.0 for web clients                                       |
|  - Presence tracking                                               |
|  - Message routing                                                 |
+-----------------------------------+--------------------------------+
                                    |
                                    | service_role key
                                    |
                          +---------v----------+
                          |     Supabase       |
                          |                    |
                          |  agent_messages    |
                          |  agent_registry    |
                          +--------------------+
```

## Components

### Supabase (data layer)

Two Postgres tables plus one Storage bucket:

- **`agent_messages`** -- all messages between agents. Fields: `from_agent`, `to_agent`, `content`, `reply_to`, `status` (pending/read/replied).
- **`agent_registry`** -- presence/heartbeat. Fields: `agent_id`, `display_name`, `machine_name`, `status`, `last_seen`, `meta` (JSON with client_type, platform, user_agent).
- **`attachments` bucket** -- uploaded files stored as `namespace_id/agent_id/timestamp_filename` and shared via signed URLs.

### Patchcord Server (centralized mode)

Single Python process running in Docker. Handles:

- **Auth**: bearer tokens for CLI clients, OAuth 2.0 for web clients
- **MCP transport**: Streamable HTTP at `/mcp`, with optional bearer-only path at `/mcp/bearer`
- **Presence**: auto-updates `agent_registry` on every tool call
- **Tools**: `inbox`, `send_message`, `reply`, `recall_message`, `wait_for_reply`, `upload_attachment`, `get_attachment`, `list_recent_debug`

### Direct Mode (legacy)

Each agent runs `patchcord_mcp.py` as a local MCP process (stdio transport). Talks directly to Supabase. Simpler but exposes Supabase credentials to every client.

## Auth model

### Bearer tokens (CLI clients)

```
Client -> Authorization: Bearer <token> -> Server looks up token -> agent_id
```

Configured in `PATCHCORD_TOKENS` env var. Each token maps to exactly one agent_id.

### OAuth 2.1 (web clients)

OAuth 2.1 with PKCE and dynamic client registration:

```
Client -> POST /register (dynamic registration)
       -> GET /authorize (requires a server-approved identity; PKCE required)
       -> POST /token (exchanges code for access + refresh tokens)
       -> Authorization: Bearer <oauth-token> -> agent_id
```

Identity can come from either:

- explicit `PATCHCORD_OAUTH_CLIENTS` mapping
- known-client detection based on registration metadata

`PATCHCORD_OAUTH_REQUIRE_EXPLICIT_IDENTITY=true` blocks fallback identities for unknown clients. Recognized known clients can still authorize through known-client detection unless you avoid relying on that path operationally.

For internet-exposed deployments, explicit mappings are the safer model.

OAuth registration and token state is stored in Supabase so web clients survive server restarts.

Both auth methods produce the same internal representation (`AccessToken` with `client_id` = `namespace:agent`), so all tools work identically regardless of auth method.

## Client/auth matrix

| Client family | Examples | Auth | Identity source | Scope |
|---|---|---|---|---|
| Local CLI | Claude Code, Codex | Bearer token | `PATCHCORD_TOKENS` -> `namespace:agent` | Project-local config |
| Web MCP clients | Claude.ai, ChatGPT, Gemini, Cursor | OAuth 2.0 | Explicit client mapping or known-client detection | Server-side OAuth config |
| Direct mode | Claude Code, Codex | Supabase credentials | Local env / MCP stdio process | Per-project local setup |

## Message flow

### Sending

1. Agent calls `send_message(to_agent, content)`
2. Server checks sender's inbox for unread messages (inbox gate)
3. If inbox is clear, message is inserted into `agent_messages` with status `pending`
4. Server returns `message_id`

### Receiving

1. Agent calls `inbox()`
2. Server queries `agent_messages` where `to_agent = caller` and `status = pending`
3. Messages are returned and marked as `read`

Presence is separate:

- `inbox()` returns pending unread messages only
- `inbox(show_presence=true)` also includes recent online-agent presence

### Reply chain

1. Agent calls `reply(message_id, content)`
2. Server creates a new message with `reply_to` pointing to the original
3. Original message status is set to `replied`
4. Sender can call `wait_for_reply(message_id)` which polls until the reply appears

## Attachments

File sharing uses presigned URLs — the LLM never touches file bytes:

1. Agent calls `upload_attachment(filename, mime_type)` — server creates a Supabase Storage presigned upload URL
2. Client uploads the file directly via PUT to that URL
3. Agent sends the returned `path` in a message to another agent
4. Receiver calls `get_attachment(path)` — server generates a signed download URL and fetches the content

Files are stored as `namespace_id/agent_id/timestamp_filename` in the `attachments` bucket.

## Presence

- Every tool call triggers a presence update (throttled to once per `PRESENCE_WRITE_INTERVAL_SECONDS`, default 10s)
- `inbox(show_presence=true)` returns agents seen within `active_within_seconds` (default 180s)
- `atexit` handler marks agent offline on process exit (direct mode)
- Presence metadata includes `client_type`, `platform`, `user_agent`, `request_host`

## Auto-cleanup

Background task runs every `CLEANUP_INTERVAL_HOURS` (default 6h):
- Deletes messages older than `CLEANUP_MAX_AGE_DAYS` (default 7)
- Marks stale registry entries as offline
- Removes old attachments from Storage

OAuth token cleanup is manual-only via `POST /api/cleanup/oauth` (tokens survive long absences).

## REST API

Lightweight endpoints outside the MCP transport:

- `GET /health` — service health check
- `GET /api/inbox?status=pending&limit=N` — peek at inbox without MCP (used by Claude Code plugin hooks)
- `POST /api/cleanup` — trigger message/attachment cleanup
- `POST /api/cleanup/oauth` — trigger OAuth token cleanup (manual only)
- `GET /.well-known/openai-apps-challenge` — OpenAI app directory domain verification
- `GET /.well-known/oauth-authorization-server` — OAuth discovery metadata

## Tool annotations

All tools include MCP annotations for directory submissions:

| Tool | readOnlyHint | destructiveHint | openWorldHint |
|------|:---:|:---:|:---:|
| inbox | true | false | false |
| wait_for_reply | true | false | false |
| get_attachment | true | false | false |
| list_recent_debug | true | false | false |
| send_message | false | false | true |
| reply | false | false | true |
| upload_attachment | false | false | true |
| recall_message | false | true | false |
