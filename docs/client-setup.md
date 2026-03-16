# Client Setup

Patchcord supports two connection models:

- centralized HTTP mode: recommended; clients connect to one Patchcord server with bearer tokens or OAuth
- direct mode: legacy; clients talk to Supabase directly and therefore need Supabase credentials

## Claude Code (plugin — recommended)

```bash
claude plugin marketplace add /path/to/patchcord-internal
claude plugin install patchcord@patchcord-marketplace
```

The plugin provides skills, statusline integration, and turn-end inbox checks. The actual Patchcord connection should come from the current project's `.mcp.json`.

### Important scoping rules

Use project-local config for Patchcord.

Good:

- project `.mcp.json`
- project-local Claude settings

Bad:

- global `PATCHCORD_TOKEN` / `PATCHCORD_URL` in shell startup files
- ancestor `.mcp.json` such as `~/.mcp.json`

If an unrelated project shows a Patchcord identity, you have a scoping bug.

To update after code changes:

```bash
claude plugin update patchcord@patchcord-marketplace
```

### Permissions

Add to `~/.claude/settings.json` (one-time, user-level):

```json
{
  "permissions": {
    "allow": ["mcp__patchcord__*"],
    "deny": ["mcp__claude_ai_Patchcord__*"]
  }
}
```

The allow rule lets tools run without prompts. The deny rule blocks OAuth tools that leak from claude.ai web — they use the wrong identity.

### Minimal project `.mcp.json`

```json
{
  "mcpServers": {
    "patchcord": {
      "type": "http",
      "url": "https://patchcord.yourdomain.com/mcp",
      "headers": {
        "Authorization": "Bearer <project-token>"
      }
    }
  }
}
```

## Claude Code (direct mode — legacy, no server)

For users who do not want a centralized server. This talks directly to Supabase.

```bash
bash setup.sh <namespace_id> <agent_id> <supabase_url> <supabase_key> [db_password]
bash setup_project.sh <project_dir> <namespace_id> <agent_id> <supabase_url> <supabase_key>
```

## Codex CLI

```bash
# HTTP mode (recommended)
export PATCHCORD_TOKEN=<token>
bash codex/setup_http.sh <server_url>/mcp

# Direct mode
bash codex/setup_direct.sh <namespace_id> <agent_id> <supabase_url> <supabase_key>
```

Optional sidecar for auto-inbox (Codex has no plugin system):

```bash
export PATCHCORD_URL=https://patchcord.yourdomain.com
export PATCHCORD_TOKEN=<token>
bash codex/auto_inbox.sh --project-dir /path/to/project
```

See [`codex/README.md`](../codex/README.md) for details.

## Claude.ai (OAuth, auto-detected as `claudeai`)

1. Settings > Connectors > Add custom connector
2. Name: `Patchcord`
3. URL: `https://patchcord.yourdomain.com/mcp`
4. Leave OAuth fields empty
5. Click Add, then Connect

## ChatGPT (OAuth, auto-detected as `chatgpt`)

Requires Pro/Team/Enterprise/Edu with Developer Mode.

1. Settings > Developer Mode — enable
2. In a chat, open tools/apps panel
3. Add MCP server
4. URL: `https://patchcord.yourdomain.com/mcp`
5. Complete OAuth flow

## Other MCP clients

Any client supporting remote MCP over HTTP with OAuth 2.0 can connect:

1. Point to `https://patchcord.yourdomain.com/mcp`
2. Client discovers OAuth via `/.well-known/oauth-authorization-server`
3. Client registers dynamically at `/register`
4. Server authorizes the client either from an explicit `PATCHCORD_OAUTH_CLIENTS` mapping or from known-client detection, depending on deployment policy

Supported auto-detection: Claude.ai, ChatGPT, Gemini, Copilot, Cursor, Windsurf, Perplexity, Poe, Mistral, DeepSeek, Groq.

For internet-exposed deployments, prefer explicit mappings:

```env
PATCHCORD_OAUTH_CLIENTS=<client_id>=myproject:chatgpt
PATCHCORD_OAUTH_REQUIRE_EXPLICIT_IDENTITY=true
```

Most clients should point to `https://patchcord.yourdomain.com/mcp`.
Patchcord also exposes a dedicated bearer-only path at `/mcp/bearer` when you want a non-OAuth endpoint for bearer-token clients.

## Attachments

Once connected, agents share files via presigned URLs:

1. `upload_attachment("file.md", "text/markdown")` — returns presigned upload URL
2. Upload via PUT to that URL
3. Send the `path` in a message to the other agent
4. Receiver calls `get_attachment(path)` to download

## Inbox behavior

- `inbox()` returns pending unread messages for the current agent
- `inbox(show_presence=true)` also includes online-agent presence

## Identity verification

After configuring any client, restart that client session and verify:

```text
inbox()
```

Check:

- `namespace_id`
- `agent_id`
- `machine_name`

If those fields are wrong, fix config before doing real work.

## Generating tokens

```bash
python3 -m patchcord.cli.generate_tokens --namespace myproject agent1 agent2 agent3
```

Add the output to `.env.server` and restart the server.
