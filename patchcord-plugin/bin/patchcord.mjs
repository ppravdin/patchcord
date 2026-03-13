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
  patchcord install            Install/update plugin globally (Claude Code)
  patchcord install --full     Install + enable full statusline (model, context%, git)
  patchcord agent              Set up MCP config for an agent in this project
  patchcord agent --codex      Set up Codex skill + MCP config in this project

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

  const flags = process.argv.slice(3);
  const fullStatusline = flags.includes("--full");

  console.log("Installing patchcord plugin into Claude Code...");

  // Register npm package as a local marketplace (idempotent)
  const marketplaceExists = run(`claude plugin marketplace list`)?.includes("patchcord");
  if (!marketplaceExists) {
    const addResult = run(`claude plugin marketplace add "${pluginRoot}"`);
    if (addResult === null) {
      console.log(`✗ Could not add marketplace. Try manually:
  claude plugin marketplace add "${pluginRoot}"
  claude plugin install patchcord`);
      process.exit(1);
    }
  }

  // Install or update the plugin from the marketplace
  const installed = run(`claude plugin list`)?.includes("patchcord");
  const result = installed
    ? run(`claude plugin update patchcord`)
    : run(`claude plugin install patchcord`);
  if (result === null && !installed) {
    console.log(`✗ Plugin install failed. Try manually:
  claude plugin marketplace add "${pluginRoot}"
  claude plugin install patchcord`);
    process.exit(1);
  }

  // Enable statusline
  const enableScript = join(pluginRoot, "scripts", "enable-statusline.sh");
  if (existsSync(enableScript)) {
    const slArg = fullStatusline ? " --full" : "";
    const slResult = run(`bash "${enableScript}"${slArg}`);
    if (slResult !== null) {
      console.log(`✓ Plugin installed. Statusline${fullStatusline ? " (full)" : ""} enabled.

${fullStatusline
  ? "Statusline shows: model │ context% │ repo (branch) │ agent@namespace │ inbox"
  : "Statusline shows: agent@namespace │ inbox\n  Tip: run \"patchcord install --full\" for model, context%, git info too."}

Run "patchcord agent" in each project to set up MCP.`);
    } else {
      console.log(`✓ Plugin installed. Statusline setup skipped (non-fatal).

Run "patchcord agent" in each project to set up MCP.`);
    }
  } else {
    console.log(`✓ Plugin installed.

Run "patchcord agent" in each project to set up MCP.`);
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
