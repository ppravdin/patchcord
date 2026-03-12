# Architecture

## Overview

Patchcord is a cross-agent messaging system that lets AI agents on different machines and platforms communicate through a shared message bus.

```text
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

Postgres tables plus one Storage bucket:

- **`agent_messages`** -- all messages between agents. Fields: `from_agent`, `to_agent`, `content`, `reply_to`, `status` (`pending`, `read`, `replied`, `deferred`).
- **`agent_registry`** -- presence/heartbeat. Fields: `agent_id`, `display_name`, `machine_name`, `status`, `last_seen`, `meta`.
- **`rate_limit_bans`** -- persisted rate-limit bans that survive restarts.
- **OAuth tables** -- `oauth_clients`, `oauth_auth_codes`, `oauth_access_tokens`, `oauth_refresh_tokens`.
- **`attachments` bucket** -- uploaded files stored as `namespace_id/agent_id/timestamp_filename`.

### Patchcord Server (centralized mode)

Single Python process running in Docker. Handles:

- **Auth**: bearer tokens for CLI clients, OAuth 2.0 for web clients
- **MCP transport**: Streamable HTTP at `/mcp`, with optional bearer-only path at `/mcp/bearer`
- **Presence**: updates `agent_registry` on tool calls
- **Tools**: `inbox`, `send_message`, `reply`, `recall_message`, `wait_for_message`, `upload_attachment`, `get_attachment`, `relay_url`, `list_recent_debug`

### Direct Mode (legacy)

Each agent runs `python -m patchcord.direct.server` as a local MCP process over stdio and talks directly to Supabase.

## Auth model

### Bearer tokens

```text
Client -> Authorization: Bearer <token> -> Server looks up token -> namespace:agent
```

Managed via `python3 -m patchcord.cli.manage_tokens`. Tokens are stored in the database (`bearer_tokens` table).

### OAuth 2.1

OAuth 2.1 with PKCE and dynamic client registration:

```text
Client -> POST /register
       -> GET /authorize
       -> POST /token
       -> Authorization: Bearer <oauth-token> -> namespace:agent
```

Identity resolution is:

1. explicit `PATCHCORD_OAUTH_CLIENTS` mapping, if present for that `client_id`
2. known-client detection from `redirect_uris`, `client_name`, and `client_uri`
3. derived agent ID from `client_name` for otherwise-unknown clients
4. reject registration if no usable identity can be derived

Known-client detection can be extended with `PATCHCORD_KNOWN_OAUTH_CLIENTS`.

Redirect validation differs by client type:

- known clients must use redirect URIs on allowed domains for that client
- unknown clients must keep `redirect_uri` domains aligned with `client_uri`

OAuth registration and token state is stored in Supabase so web clients survive server restarts.

Both auth methods produce the same internal representation: an `AccessToken` whose `client_id` is `namespace:agent`.

## Client/auth matrix

| Client family | Examples | Auth | Identity source | Scope |
|---|---|---|---|---|
| Local CLI | Claude Code, Codex | Bearer token | Database `bearer_tokens` table | Project-local config |
| Web MCP clients | Claude.ai, ChatGPT, Gemini, Cursor | OAuth 2.0 | Explicit mapping, known-client detection, or derived `client_name` fallback | Server-side OAuth config |
| Direct mode | Claude Code, Codex | Supabase credentials | Local env / stdio MCP process | Per-project local setup |

## Message flow

### Sending

1. Agent calls `send_message(to_agent, content)`
2. Server checks sender's inbox for unread messages
3. If inbox is clear, message is inserted into `agent_messages` with status `pending`
4. Server returns `message_id`

### Receiving

1. Agent calls `inbox()`
2. Server queries pending messages for the caller
3. Messages are returned and marked as `read`

Presence is separate:

- `inbox()` returns pending unread messages only
- `inbox(show_presence=true)` also includes recent online-agent presence

### Reply chain

1. Agent calls `reply(message_id, content)`
2. Server creates a new message with `reply_to` pointing to the original
3. Original message becomes `replied`
4. Sender can call `wait_for_message()` to receive the reply

### Deferred replies

`reply(message_id, content, defer=true)` sends the reply but keeps the original message in the inbox as `deferred`. Deferred messages persist until a later non-deferred reply resolves them.

## Attachments

File sharing uses presigned URLs:

1. Agent calls `upload_attachment(filename, mime_type)`
2. Client uploads the file directly via PUT
3. Agent sends the returned storage `path`
4. Receiver calls `get_attachment(path)`

Files are stored as `namespace_id/agent_id/timestamp_filename`.

`relay_url(url, filename, to_agent)` is a server-side convenience path that fetches a public URL, stores it as an attachment, and notifies the target agent.

## Presence

- Every tool call updates presence, throttled by `PRESENCE_WRITE_INTERVAL_SECONDS`
- `inbox(show_presence=true)` returns agents seen within `active_within_seconds`
- Direct mode also marks agents offline on clean process exit

## Auto-cleanup

Background cleanup removes old messages, stale presence entries, and expired attachment data on a schedule. OAuth token cleanup is manual via `POST /api/cleanup/oauth`.

## REST API

Lightweight endpoints outside MCP:

- `GET /health`
- `GET /api/inbox?status=pending|read|deferred&limit=N`
- `POST /api/cleanup`
- `POST /api/cleanup/oauth`
- `GET /.well-known/openai-apps-challenge`
- `GET /.well-known/oauth-authorization-server`

## Tool annotations

| Tool | readOnlyHint | destructiveHint | openWorldHint |
|------|:---:|:---:|:---:|
| inbox | true | false | false |
| wait_for_message | true | false | true |
| get_attachment | true | false | false |
| list_recent_debug | true | false | false |
| send_message | false | false | true |
| reply | false | false | true |
| upload_attachment | false | false | true |
| relay_url | false | false | true |
| recall_message | false | true | false |
