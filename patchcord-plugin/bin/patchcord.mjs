#!/usr/bin/env node

import { existsSync, mkdirSync, cpSync, readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pluginRoot = join(__dirname, "..");
const cmd = process.argv[2];

if (!cmd || cmd === "help" || cmd === "--help" || cmd === "-h") {
  console.log(`patchcord — agent messaging for Claude Code & Codex

Usage:
  patchcord init              Auto-detect and set up for current project
  patchcord init --codex      Set up Codex skill in current project
  patchcord init --claude     Set up Claude Code plugin
  patchcord plugin-path       Print path to Claude Code plugin directory

Setup after init:
  1. Add your MCP server to .mcp.json (Claude Code) or ~/.codex/config.toml (Codex)
  2. Start your agent — patchcord tools are available immediately`);
  process.exit(0);
}

if (cmd === "plugin-path") {
  console.log(pluginRoot);
  process.exit(0);
}

if (cmd === "init") {
  const flag = process.argv[3];
  const cwd = process.cwd();

  if (flag === "--codex" || (!flag && existsSync(join(cwd, ".agents")))) {
    // Codex setup: copy SKILL.md to .agents/skills/patchcord/
    const dest = join(cwd, ".agents", "skills", "patchcord");
    mkdirSync(dest, { recursive: true });
    cpSync(join(pluginRoot, "codex", "SKILL.md"), join(dest, "SKILL.md"));
    console.log(`Codex skill installed: ${dest}/SKILL.md

Next: add patchcord MCP server to ~/.codex/config.toml:

  [mcp_servers.patchcord]
  url = "https://YOUR_SERVER/mcp"
  bearer_token_env_var = "PATCHCORD_TOKEN"
  http_headers = { "X-Patchcord-Client-Type" = "codex" }`);
  } else if (flag === "--claude" || !flag) {
    // Claude Code setup: install plugin
    console.log(`Claude Code plugin path: ${pluginRoot}

Install with:
  claude plugin install --path "${pluginRoot}"

Then add .mcp.json to your project:

  {
    "mcpServers": {
      "patchcord": {
        "type": "http",
        "url": "https://YOUR_SERVER/mcp",
        "headers": {
          "Authorization": "Bearer YOUR_TOKEN",
          "X-Patchcord-Client-Type": "claude_code"
        }
      }
    }
  }`);
  }
  process.exit(0);
}

console.error(`Unknown command: ${cmd}. Run 'patchcord help' for usage.`);
process.exit(1);
