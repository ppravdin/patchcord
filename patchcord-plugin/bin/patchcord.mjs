#!/usr/bin/env node

import { existsSync, mkdirSync, cpSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { execSync } from "child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pluginRoot = join(__dirname, "..");
const cmd = process.argv[2];

function run(cmd) {
  try {
    return execSync(cmd, { stdio: "pipe", encoding: "utf-8" }).trim();
  } catch {
    return null;
  }
}

if (!cmd || cmd === "help" || cmd === "--help" || cmd === "-h") {
  console.log(`patchcord — agent messaging for Claude Code & Codex

Commands:
  patchcord install       Install/update plugin globally (Claude Code)
  patchcord agent         Set up MCP config for an agent in this project
  patchcord agent --codex Set up Codex skill + MCP config in this project

Run "patchcord install" once. Run "patchcord agent" in each project.`);
  process.exit(0);
}

if (cmd === "plugin-path") {
  console.log(pluginRoot);
  process.exit(0);
}

// ── install: global plugin + skills (idempotent) ──────────────
if (cmd === "install") {
  const hasClaude = run("which claude");
  if (!hasClaude) {
    console.log(`Claude Code CLI not found. Install it first:
  https://claude.ai/code

Then run: patchcord install`);
    process.exit(1);
  }

  console.log("Installing patchcord plugin into Claude Code...");
  const result = run(`claude plugin install --path "${pluginRoot}"`);
  if (result !== null) {
    console.log(`✓ Plugin installed. Skills, hooks, and statusline are active.

Run "patchcord agent" in each project to set up MCP.`);
  } else {
    console.log(`✗ Plugin install failed. Try manually:
  claude plugin install --path "${pluginRoot}"`);
    process.exit(1);
  }
  process.exit(0);
}

// ── agent: per-project MCP setup ──────────────────────────────
if (cmd === "agent") {
  const flag = process.argv[3];
  const cwd = process.cwd();

  if (flag === "--codex" || (!flag && existsSync(join(cwd, ".agents")))) {
    // Codex: copy skill + print MCP config
    const dest = join(cwd, ".agents", "skills", "patchcord");
    mkdirSync(dest, { recursive: true });
    cpSync(join(pluginRoot, "codex", "SKILL.md"), join(dest, "SKILL.md"));
    console.log(`✓ Codex skill installed: ${dest}/SKILL.md

Add to ~/.codex/config.toml:

  [mcp_servers.patchcord]
  url = "https://YOUR_SERVER/mcp"
  bearer_token_env_var = "PATCHCORD_TOKEN"
  http_headers = { "X-Patchcord-Client-Type" = "codex" }`);
  } else {
    // Claude Code: print .mcp.json template
    console.log(`Add .mcp.json to this project:

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
  }

Or use the CLI:

  claude mcp add patchcord "https://YOUR_SERVER/mcp" \\
    --transport http -s project \\
    -H "Authorization: Bearer YOUR_TOKEN" \\
    -H "X-Patchcord-Client-Type: claude_code"`);
  }
  process.exit(0);
}

// ── back-compat: init → install + agent ───────────────────────
if (cmd === "init") {
  console.log(`"patchcord init" is now two commands:

  patchcord install    Install/update plugin globally (once)
  patchcord agent      Set up MCP for this project (per project)`);
  process.exit(0);
}

console.error(`Unknown command: ${cmd}. Run 'patchcord help' for usage.`);
process.exit(1);
