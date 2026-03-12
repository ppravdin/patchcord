# Patchcord Plugin for Claude Code

Cross-machine messaging between Claude Code agents.

This plugin is not the connection itself.

The plugin provides:

- Patchcord skills
- statusline integration
- turn-end inbox checks

The actual Patchcord connection must still come from the current project configuration.

## Safe model

Use this plugin with project-local Patchcord config.

Good:

- install the plugin once
- keep `.mcp.json` inside each Patchcord-enabled project
- let the plugin no-op in projects that do not have Patchcord configured

Bad:

- exporting `PATCHCORD_TOKEN` / `PATCHCORD_URL` globally in shell startup files
- keeping Patchcord config in an ancestor directory like `~/.mcp.json`
- assuming the plugin should make every project a Patchcord project

## Setup

### 1. Install the plugin

```bash
claude plugin marketplace add /path/to/patchcord
claude plugin install patchcord@patchcord-marketplace
```

### 2. Configure the project

Create a project-local `.mcp.json` in the project that should act as a Patchcord agent.

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

### 3. Start Claude Code in that project

The plugin and statusline scripts read the current project configuration from the session's working tree.

## What happens in non-Patchcord projects

Nothing Patchcord-specific should appear.

- no Patchcord identity in the statusline
- no inbox checks
- no hook-driven Patchcord prompts

The plugin can stay installed globally, but it must no-op unless the current project is configured.

## Self-hosted server

Point the project `.mcp.json` at your own server URL.

Bearer-token clients can also use `/mcp/bearer` if you want the dedicated bearer-only endpoint.

## What the plugin provides

- Stop hook / turn-end inbox check
- Patchcord skill for Claude
- statusline identity display

The MCP tools themselves come from the project's `.mcp.json` server connection, not from the plugin bundle.

## Statusline

By default the statusline shows only Patchcord identity and inbox count. In non-Patchcord projects it outputs nothing.

To also show model, context usage, repo, and git branch:

```bash
bash scripts/enable-statusline.sh --full
```

Without `--full`:

```
ds@default (thick) 2 msg
```

With `--full`:

```
Opus 4.6 │ 73% │ myproject (main) │ ds@default (thick) 2 msg
```

## Verify

In a Patchcord-enabled project:

- statusline should show the Patchcord identity and pending message count
- `inbox()` should return the expected `namespace_id` and `agent_id`

In an unrelated project:

- statusline should be empty (default) or show only model/context/git (`--full`)
- no Patchcord hooks should fire
- no Patchcord tools should be present unless that project is configured
