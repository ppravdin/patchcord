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

- exporting `PATCHCORD_TOKEN` / `PATCHCORD_URL` globally in `~/.bashrc`, `~/.profile`, or similar
- keeping Patchcord config in an ancestor directory like `~/.mcp.json`
- assuming the plugin should make every project a Patchcord project

## Setup

### 1. Install the plugin

```bash
claude plugin marketplace add /path/to/patchcord-internal
claude plugin install patchcord@patchcord-marketplace
```

### 2. Configure the project

Create a project-local `.mcp.json` in the project that should act as a Patchcord agent.

Example:

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

### 3. Restart Claude Code in that project

The plugin and statusline scripts read the current project configuration when the session starts.

## What happens in non-Patchcord projects

Nothing Patchcord-specific should appear.

- no Patchcord identity in the statusline
- no inbox checks
- no hook-driven Patchcord prompts

The plugin is allowed to stay installed globally, but it must no-op unless the current project is configured.

## Self-hosted server

The project `.mcp.json` should point to your own server URL:

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

## Verify

In a Patchcord-enabled project:

- statusline should show the Patchcord identity
- `inbox()` should return the expected `namespace_id` and `agent_id`

In an unrelated project:

- statusline should not show Patchcord identity
- no Patchcord hooks should fire
- no Patchcord tools should be present unless that project is configured
