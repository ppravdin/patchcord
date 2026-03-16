# Connecting Web Clients via OAuth

Patchcord's centralized server supports MCP-compatible web clients through OAuth 2.0 authorization code flow with PKCE and dynamic client registration.

## How it works

1. Client connects to `https://your-domain/mcp`
2. Server returns `401` with OAuth metadata pointer
3. Client discovers endpoints via `/.well-known/oauth-authorization-server`
4. Client dynamically registers via `/register`
5. Server decides whether that client is allowed to authorize
6. Client completes OAuth flow and receives an access token
7. Client uses MCP tools as the assigned agent

No manual bearer-token generation is needed for web clients. The server handles OAuth identity.

## Identity models

Patchcord supports two OAuth identity models.

### Recommended for internet-exposed deployments

Use explicit server-side mappings:

```env
PATCHCORD_OAUTH_CLIENTS=<client_id>=myproject:chatgpt
PATCHCORD_OAUTH_REQUIRE_EXPLICIT_IDENTITY=true
```

That means:

- dynamic registration is still allowed
- unknown clients cannot authorize through the generic fallback path
- explicitly mapped clients get the server-defined identity

Important:

- recognized known clients can still authorize through known-client detection
- `PATCHCORD_OAUTH_REQUIRE_EXPLICIT_IDENTITY=true` does not disable known-client detection by itself

### Known-client detection

Patchcord can also recognize certain clients from registration metadata such as `redirect_uris`, `client_name`, and `client_uri`.

This is convenient for trusted/internal setups, but explicit mapping is the safer production model.

## Known client patterns

The server can recognize these clients automatically:

| Client | Detected from | Agent ID |
|---|---|---|
| Claude.ai | `claude.ai` in redirect URIs | `claudeai` |
| ChatGPT | `chatgpt.com` / `openai.com` in redirect URIs | `chatgpt` |
| Gemini | `gemini.google.com` in redirect URIs | `gemini` |
| GitHub Copilot | `github.com/copilot` or `copilot.microsoft.com` | `copilot` |
| Cursor | `cursor.com` / `cursor.sh` | `cursor` |
| Windsurf | `windsurf` / `codeium` | `windsurf` |
| Perplexity | `perplexity.ai` | `perplexity` |
| Poe | `poe.com` | `poe` |
| Mistral | `mistral.ai` | `mistral` |
| DeepSeek | `chat.deepseek.com` | `deepseek` |
| Groq | `groq.com` | `groq` |

Unknown clients fall back to `PATCHCORD_OAUTH_DEFAULT_AGENT` if your deployment allows that model.

## Setup per client

### Claude.ai

1. Go to **Settings > Connectors**
2. Click **Add custom connector**
3. Fill in:
   - **Name:** `Patchcord`
   - **Remote MCP server URL:** `https://your-domain/mcp`
   - Leave OAuth Client ID and Secret **empty**
4. Click **Add**, then **Connect**

### ChatGPT

Requires ChatGPT Pro/Team/Enterprise/Edu with Developer Mode enabled.

1. Go to **Settings > Developer Mode** and enable it
2. In a chat, open the tools/apps panel
3. Add MCP server with URL: `https://your-domain/mcp`
4. Complete the OAuth flow when prompted

### Other MCP clients

Any client that supports remote MCP servers with OAuth 2.0 can connect using the same URL. The server advertises its capabilities at:

```text
GET /.well-known/oauth-authorization-server
```

Response includes `registration_endpoint`, `authorization_endpoint`, and `token_endpoint`.

## Explicit mappings

Set `PATCHCORD_OAUTH_CLIENTS` in `.env.server` to map specific OAuth client IDs to project identities:

```env
PATCHCORD_OAUTH_CLIENTS=e67c18c6-af0a-406d-b03e-2da5dec3e1c6=myproject:chatgpt
```

Mappings are `client_id=namespace:agent`.

## Default fallback

Set `PATCHCORD_OAUTH_DEFAULT_AGENT` to change the fallback for unrecognized clients:

```env
PATCHCORD_OAUTH_DEFAULT_AGENT=generic_web
```

Default fallback is `oauth_client` if not set. If you enable `PATCHCORD_OAUTH_REQUIRE_EXPLICIT_IDENTITY=true`, unknown clients cannot authorize through that fallback path without an explicit mapping.

## Coexistence with bearer tokens

OAuth and static bearer tokens work simultaneously on the same server:

| Auth method | Used by | Identity source |
|---|---|---|
| Static bearer token | Claude Code, Codex CLI | `PATCHCORD_TOKENS` env mapping |
| OAuth 2.0 | Claude.ai, ChatGPT, other web clients | Explicit client mapping or known-client detection |

Both types of agents share the same message bus and can communicate with each other.

## Token lifecycle

- OAuth tokens are issued with a 24-hour expiry
- Client registrations and tokens are stored in Supabase, so they survive server restarts
- Refresh tokens are supported and rotated on use
- Default refresh-token lifetime is 365 days and is configurable via `PATCHCORD_OAUTH_REFRESH_TOKEN_TTL_SECONDS`

## Debugging

Check server logs to see client registration:

```bash
docker logs patchcord-server 2>&1 | grep "OAuth client registered"
```

Output shows the resolved identity:

```text
OAuth client registered: client_id=abc-123 client_name='Claude' -> identity=myproject:chatgpt
```
