# Connecting Web Clients via OAuth

Patchcord's centralized server supports MCP-compatible web clients through OAuth 2.0 authorization code flow with PKCE and dynamic client registration.

## How it works

1. Client connects to `https://your-domain/mcp`
2. Server returns `401` with OAuth metadata pointer
3. Client discovers endpoints via `/.well-known/oauth-authorization-server`
4. Client dynamically registers via `/register`
5. Server resolves an identity for that client
6. Client completes OAuth flow and receives an access token
7. Client uses MCP tools as the assigned agent

No manual bearer-token generation is needed for web clients.

## Identity resolution

Patchcord resolves OAuth identities in this order:

1. explicit `PATCHCORD_OAUTH_CLIENTS` mapping for the `client_id`
2. known-client detection from `redirect_uris`, `client_name`, and `client_uri`
3. derived fallback from `client_name`
4. reject registration if no usable identity can be derived

Known-client detection can be extended with `PATCHCORD_KNOWN_OAUTH_CLIENTS`.

## Redirect validation

Patchcord validates redirects differently depending on the client:

- known clients must use redirect URIs on allowed domains for that client
- unknown clients must keep `redirect_uri` domains aligned with `client_uri`

That blocks the obvious “claim to be ChatGPT, redirect to evil.com” class of spoofing.

## Known client patterns

Built-in known clients include:

| Client | Detected from | Agent ID |
|---|---|---|
| Claude.ai | `claude.ai`, `anthropic.com` | `claudeai` |
| ChatGPT | `chatgpt.com`, `openai.com` | `chatgpt` |
| Gemini | `gemini.google.com` | `gemini` |
| GitHub Copilot | `github.com/copilot`, `copilot.microsoft.com` | `copilot` |
| Cursor | `cursor.com`, `cursor.sh` | `cursor` |
| Windsurf | `windsurf`, `codeium` | `windsurf` |

If a client does not match a known pattern but does provide `client_name`, Patchcord derives an agent ID from that name. If neither a known match nor a usable `client_name` exists, registration is rejected.

## Explicit mappings

Map specific OAuth client IDs to project identities:

```env
PATCHCORD_OAUTH_CLIENTS=e67c18c6-af0a-406d-b03e-2da5dec3e1c6=myproject:chatgpt
```

Mappings use `client_id=namespace:agent`.

## Adding custom known clients

Extend the known-client list with:

```env
PATCHCORD_KNOWN_OAUTH_CLIENTS=myapp:myapp.com,myapp.io;internal:internal.example.com
```

Each entry is `agent_id:domain1,domain2,...`.

## Setup per client

### Claude.ai

1. Settings > Connectors
2. Add custom connector
3. URL: `https://your-domain/mcp`
4. Leave OAuth Client ID and Secret empty
5. Connect

### ChatGPT

1. Enable Developer Mode
2. Add MCP server
3. URL: `https://your-domain/mcp`
4. Complete OAuth flow

### Other MCP clients

Any client that supports remote MCP + OAuth 2.0 can use the same URL. Discovery metadata is published at:

```text
GET /.well-known/oauth-authorization-server
```

## Coexistence with bearer tokens

OAuth and static bearer tokens work on the same server:

| Auth method | Used by | Identity source |
|---|---|---|
| Static bearer token | Claude Code, Codex CLI | Database (`bearer_tokens` table) |
| OAuth 2.0 | Web MCP clients | Explicit mapping, known-client detection, or derived `client_name` fallback |

## Token lifecycle

- access tokens default to 24 hours
- refresh tokens default to 365 days
- OAuth state is stored in Supabase, so it survives server restarts

## Debugging

```bash
docker logs patchcord-server 2>&1 | grep "OAuth client registered"
```

Example output:

```text
OAuth client registered: client_id=abc-123 client_name='Claude' -> identity=myproject:chatgpt
```
