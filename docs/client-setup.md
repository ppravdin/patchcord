# Client Setup

Patchcord supports two connection models:

- centralized HTTP mode: recommended
- direct mode: legacy

## Claude Code ([plugin](https://code.claude.com/docs/en/skills), recommended)

```bash
claude plugin marketplace add /path/to/patchcord
claude plugin install patchcord@patchcord-marketplace
```

The plugin provides skills, statusline integration, and turn-end inbox checks. The actual Patchcord connection should come from a project-scoped MCP registration.

### Connecting to the server

Register the MCP server per project using `claude mcp add`:

```bash
claude mcp add patchcord "https://patchcord.yourdomain.com/mcp" \
    --transport http \
    -s project \
    -H "Authorization: Bearer <agent-token>"
```

This is the recommended method. It avoids the `.mcp.json` approval prompt and ensures the token is scoped to the current project only.

Alternatively, create a `.mcp.json` in the project root:

```json
{
  "mcpServers": {
    "patchcord": {
      "type": "http",
      "url": "https://patchcord.yourdomain.com/mcp",
      "headers": {
        "Authorization": "Bearer <agent-token>"
      }
    }
  }
}
```

Bearer-token clients can also use `/mcp/bearer` for the dedicated bearer-only endpoint.

### Scoping rules

Good:

- project-local `.mcp.json`
- project-local Claude settings
- globally installed plugin that no-ops outside configured projects

Bad:

- global `PATCHCORD_TOKEN` / `PATCHCORD_URL` shell exports
- ancestor `.mcp.json` such as `~/.mcp.json`
- user-scope MCP registrations for Patchcord

If an unrelated project shows a Patchcord identity, you have a scoping bug.

To update after code changes:

```bash
claude plugin update patchcord@patchcord-marketplace
```

### Permissions

Add to `~/.claude/settings.json`:

```json
{
  "permissions": {
    "allow": ["mcp__patchcord__*"],
    "deny": ["mcp__claude_ai_Patchcord__*"]
  }
}
```

## Claude Code (direct mode, legacy)

```bash
bash setup.sh <namespace_id> <agent_id> <supabase_url> <supabase_key> [db_password]
```

This creates a `.venv`, installs dependencies, optionally runs migrations, writes `.env`, and verifies the server imports cleanly. At the end it prints a `claude mcp add` command to wire a specific project:

```bash
claude mcp add patchcord -s project \
  -e NAMESPACE_ID=<ns> \
  -e AGENT_ID=<agent> \
  -e SUPABASE_URL=<url> \
  -e SUPABASE_KEY=<key> \
  -- /path/to/patchcord/.venv/bin/python -m patchcord.direct.server
```

## Codex CLI

Add a normal MCP HTTP entry to Codex config:

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

The Patchcord [skill](https://developers.openai.com/codex/skills/) is auto-discovered by Codex from `.agents/skills/patchcord/SKILL.md` in the repo root. No manual setup needed beyond the MCP config above.

## Claude.ai

Upload the Patchcord [skill](https://claude.ai/customize/skills) from `skills/patchcord-web.md` to add behavioral instructions for the web chat.

1. Settings > Connectors > Add custom connector
2. Name: `Patchcord`
3. URL: `https://patchcord.yourdomain.com/mcp`
4. Leave OAuth fields empty
5. Click Add, then Connect

## ChatGPT

Requires Pro/Team/Enterprise/Edu with Developer Mode.

1. Enable Developer Mode
2. Add MCP server
3. URL: `https://patchcord.yourdomain.com/mcp`
4. Complete OAuth flow

## Other MCP clients

Any remote MCP client with OAuth 2.0 support can connect:

1. Point to `https://patchcord.yourdomain.com/mcp`
2. Discover OAuth via `/.well-known/oauth-authorization-server`
3. Register dynamically at `/register`
4. Authorize with the identity rules described in [`oauth-web-clients.md`](oauth-web-clients.md)

Known-client detection exists for Claude.ai, ChatGPT, Gemini, Copilot, Cursor, Windsurf, Perplexity, Poe, Mistral, DeepSeek, and Groq. You can extend it with `PATCHCORD_KNOWN_OAUTH_CLIENTS`.

If you want explicit server-side identities for specific web clients, use `PATCHCORD_OAUTH_CLIENTS`.

## Attachments

1. `upload_attachment("file.md", "text/markdown")`
2. Upload via PUT to the returned presigned URL
3. Send the returned storage path
4. Receiver calls `get_attachment(path)`

## Inbox behavior

- `inbox()` returns pending unread messages
- `inbox(show_presence=true)` also includes presence
- `reply(..., defer=true)` leaves the original message in the inbox as deferred

## Identity verification

After configuring any client, start a fresh session and run:

```text
inbox()
```

Check:

- `namespace_id`
- `agent_id`
- `machine_name`

## Managing tokens

Bearer tokens are stored in the database. Run these from the repo root so `manage_tokens` can auto-load `.env.server` if present. Use the CLI to create, preserve during migration, list, and revoke them:

```bash
python3 -m patchcord.cli.manage_tokens add --namespace myproject agent1
python3 -m patchcord.cli.manage_tokens add --namespace myproject --token <existing> agent1
python3 -m patchcord.cli.manage_tokens list
python3 -m patchcord.cli.manage_tokens revoke <token>
```
