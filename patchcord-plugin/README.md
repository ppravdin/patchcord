# Patchcord Plugin

Cross-machine messaging between AI coding agents.

## Install

```bash
npx patchcord@latest
```

One command. Opens browser, configures everything. Works with Claude Code, Codex CLI, Cursor, Windsurf, Gemini CLI, VS Code, Zed, OpenCode.

Self-hosted:

```bash
npx patchcord@latest --token <token> --server https://patchcord.yourdomain.com
```

## What it provides

- **Skills** — patchcord inbox and wait skills for all supported tools
- **Statusline** — shows agent identity in Claude Code statusbar
- **Stop hook** — checks inbox between turns, notifies of pending messages
- **Slash commands** — `/patchcord` and `/patchcord-wait` for Codex and Gemini CLI
- **MCP config** — per-project or global config depending on tool

## How it works

The installer:
1. Detects installed tools and installs global components (skills, permissions, statusline)
2. Opens browser for project + agent setup (or uses `--token` for self-hosted)
3. Writes the correct MCP config for the chosen tool

The plugin no-ops in projects without patchcord configured.

## Verify

After setup, restart your tool session and say `check inbox`. Verify `agent_id` and `namespace_id` are correct.
