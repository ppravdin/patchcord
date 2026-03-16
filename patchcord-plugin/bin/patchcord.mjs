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

if (cmd === "help" || cmd === "--help" || cmd === "-h") {
  console.log(`patchcord — agent messaging for AI coding agents

Usage:
  npx patchcord@latest              Full setup (global + project) — run in your project folder
  npx patchcord@latest --full       Same + full statusline (model, context%, git)
  npx patchcord@latest skill apply  Fetch custom skill from web console

That's it. One command does everything.`);
  process.exit(0);
}

if (cmd === "plugin-path") {
  console.log(pluginRoot);
  process.exit(0);
}

// ── main flow: global setup + project setup (or just install/agent for back-compat) ──
if (!cmd || cmd === "install" || cmd === "agent") {
  const flags = process.argv.slice(3);
  const fullStatusline = flags.includes("--full");
  const { readFileSync, writeFileSync } = await import("fs");

  console.log(`
  ___  ____ ___ ____ _  _ ____ ____ ____ ___
  |__] |__|  |  |    |__| |    |  | |__/ |  \\
  |    |  |  |  |___ |  | |___ |__| |  \\ |__/

          Messenger for AI agents.
`);

  const dim = "\x1b[2m";
  const green = "\x1b[32m";
  const red = "\x1b[31m";
  const cyan = "\x1b[36m";
  const yellow = "\x1b[33m";
  const white = "\x1b[37m";
  const bold = "\x1b[1m";
  const r = "\x1b[0m";

  // ── Global setup (silent if nothing changed) ──
  let globalChanges = [];

  // Claude Code
  const hasClaude = run("which claude");
  if (hasClaude) {
    const marketplaceExists = run(`claude plugin marketplace list`)?.includes("patchcord");
    if (!marketplaceExists) {
      run(`claude plugin marketplace add "${pluginRoot}"`);
      const installed = run(`claude plugin list`)?.includes("patchcord");
      installed ? run(`claude plugin update patchcord`) : run(`claude plugin install patchcord`);
      globalChanges.push("Claude Code plugin installed");
    }

    const claudeSettings = join(process.env.HOME || "", ".claude", "settings.json");
    if (existsSync(claudeSettings)) {
      try {
        const settings = JSON.parse(readFileSync(claudeSettings, "utf-8"));
        if (!settings.permissions) settings.permissions = {};
        if (!settings.permissions.allow) settings.permissions.allow = [];
        if (!settings.permissions.deny) settings.permissions.deny = [];
        let changed = false;
        if (!settings.permissions.allow.includes("mcp__patchcord__*")) {
          settings.permissions.allow.push("mcp__patchcord__*");
          changed = true;
        }
        for (const pattern of ["mcp__claude_ai_Patchcord__*", "mcp__claude_ai_patchcord__*"]) {
          if (!settings.permissions.deny.includes(pattern)) {
            settings.permissions.deny.push(pattern);
            changed = true;
          }
        }
        if (changed) {
          writeFileSync(claudeSettings, JSON.stringify(settings, null, 2) + "\n");
          globalChanges.push("Permissions configured");
        }
      } catch (e) {
        globalChanges.push(`✗ Settings error: ${e.message}`);
      }
    }

    const enableScript = join(pluginRoot, "scripts", "enable-statusline.sh");
    if (existsSync(enableScript)) {
      const slArg = fullStatusline ? " --full" : "";
      const slResult = run(`bash "${enableScript}"${slArg}`);
      if (slResult !== null && slResult.includes("statusline")) {
        globalChanges.push(`Statusline${fullStatusline ? " (full)" : ""} enabled`);
      }
    }
  }

  // Cursor
  const cursorSkillDir = join(process.env.HOME || "", ".cursor", "skills-cursor", "patchcord");
  const cursorSkillsRoot = join(process.env.HOME || "", ".cursor", "skills-cursor");
  if (existsSync(cursorSkillsRoot) && !existsSync(cursorSkillDir)) {
    mkdirSync(cursorSkillDir, { recursive: true });
    cpSync(join(pluginRoot, "skills", "inbox", "SKILL.md"), join(cursorSkillDir, "SKILL.md"));
    globalChanges.push("Cursor skill installed");
  }

  // Windsurf
  const windsurfSkillDir = join(process.env.HOME || "", ".codeium", "windsurf", "skills", "patchcord");
  const windsurfSkillsRoot = join(process.env.HOME || "", ".codeium", "windsurf", "skills");
  if (existsSync(join(process.env.HOME || "", ".codeium", "windsurf")) && !existsSync(windsurfSkillDir)) {
    mkdirSync(windsurfSkillDir, { recursive: true });
    cpSync(join(pluginRoot, "skills", "inbox", "SKILL.md"), join(windsurfSkillDir, "SKILL.md"));
    globalChanges.push("Windsurf skill installed");
  }

  // Codex CLI
  const codexConfig = join(process.env.HOME || "", ".codex", "config.toml");
  if (existsSync(codexConfig)) {
    const content = readFileSync(codexConfig, "utf-8");
    if (!content.includes("[apps.patchcord]")) {
      writeFileSync(codexConfig, content.trimEnd() + "\n\n[apps.patchcord]\nenabled = false\n");
      globalChanges.push("Codex ChatGPT app conflict prevented");
    }
  }

  // Only show global changes if something actually changed
  if (globalChanges.length > 0) {
    console.log(`${dim}Global setup:${r}`);
    for (const change of globalChanges) {
      const icon = change.startsWith("✗") ? "" : "  ✓ ";
      console.log(`${icon}${change}`);
    }
  }

  if (!hasClaude && !existsSync(codexConfig)) {
    console.log(`${dim}No Claude Code or Codex CLI detected — skipping global setup.${r}`);
  }

  // ── project setup (inline, not a separate command) ──────────
  const cwd = process.cwd();
  const { createInterface } = await import("readline");

  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const ask = (q) => new Promise((resolve) => rl.question(q, resolve));

  console.log(`\n${bold}Which tool are you setting up?${r}\n`);
  console.log(`  ${cyan}1.${r} Claude Code`);
  console.log(`  ${cyan}2.${r} Codex CLI`);
  console.log(`  ${cyan}3.${r} Cursor`);
  console.log(`  ${cyan}4.${r} Windsurf\n`);

  const choice = (await ask(`${dim}Choose (1/2/3/4):${r} `)).trim();
  const isCodex = choice === "2";
  const isCursor = choice === "3";
  const isWindsurf = choice === "4";

  if (!["1", "2", "3", "4"].includes(choice)) {
    console.error("Invalid choice.");
    rl.close();
    process.exit(1);
  }

  if (isWindsurf) {
    console.log(`\n  ${yellow}Note: Windsurf uses global config — applies to all projects.${r}`);
  } else {
    console.log(`\n${dim}Project folder:${r} ${bold}${cwd}${r}`);
    console.log(`${dim}Config will be created here. Run this in your project folder.${r}`);
    const proceed = (await ask(`${dim}Continue? (Y/n):${r} `)).trim().toLowerCase();
    if (proceed === "n" || proceed === "no") {
      rl.close();
      process.exit(0);
    }
  }


  // Check if already configured
  if (!isCodex) {
    const mcpPath = join(cwd, ".mcp.json");
    if (existsSync(mcpPath)) {
      try {
        const existing = JSON.parse(readFileSync(mcpPath, "utf-8"));
        if (existing.mcpServers?.patchcord) {
          const existingToken = existing.mcpServers.patchcord.headers?.Authorization || "";
          console.log(`\n  ${yellow}⚠ Claude Code already configured in this project${r}`);
          console.log(`  ${dim}${mcpPath}${r}`);
          const replace = (await ask(`  ${dim}Replace? (y/N):${r} `)).trim().toLowerCase();
          if (replace !== "y" && replace !== "yes") {
            console.log("Keeping existing config.");
            rl.close();
            process.exit(0);
          }
        }
      } catch {}
    }
  } else if (isCursor) {
    const cursorPath = join(cwd, ".cursor", "mcp.json");
    if (existsSync(cursorPath)) {
      try {
        const existing = JSON.parse(readFileSync(cursorPath, "utf-8"));
        if (existing.mcpServers?.patchcord) {
          console.log(`\n  ${yellow}⚠ Cursor already configured in this project${r}`);
          console.log(`  ${dim}${cursorPath}${r}`);
          const replace = (await ask(`  ${dim}Replace? (y/N):${r} `)).trim().toLowerCase();
          if (replace !== "y" && replace !== "yes") {
            console.log("Keeping existing config.");
            rl.close();
            process.exit(0);
          }
        }
      } catch {}
    }
    // Warn about global config conflict
    const globalCursor = join(process.env.HOME || "", ".cursor", "mcp.json");
    if (existsSync(globalCursor)) {
      try {
        const global = JSON.parse(readFileSync(globalCursor, "utf-8"));
        if (global.mcpServers?.patchcord) {
          console.log(`\n  ${yellow}⚠ Patchcord is also configured globally in Cursor${r}`);
          console.log(`  ${dim}${globalCursor}${r}`);
          console.log(`  ${yellow}Having both global AND per-project will cause duplicate tool calls.${r}`);
          console.log(`  ${dim}Remove patchcord from global config: Cursor Settings → MCP → remove patchcord${r}`);
        }
      } catch {}
    }
  } else if (isWindsurf) {
    // Windsurf is global only
    const wsPath = join(process.env.HOME || "", ".codeium", "windsurf", "mcp_config.json");
    if (existsSync(wsPath)) {
      try {
        const content = readFileSync(wsPath, "utf-8").trim();
        const existing = content ? JSON.parse(content) : {};
        if (existing.mcpServers?.patchcord) {
          console.log(`\n  ${yellow}⚠ Windsurf already configured${r}`);
          console.log(`  ${dim}${wsPath}${r}`);
          const replace = (await ask(`  ${dim}Replace? (y/N):${r} `)).trim().toLowerCase();
          if (replace !== "y" && replace !== "yes") {
            console.log("Keeping existing config.");
            rl.close();
            process.exit(0);
          }
        }
      } catch {}
    }
  } else {
    const configPath = join(cwd, ".codex", "config.toml");
    if (existsSync(configPath)) {
      const content = readFileSync(configPath, "utf-8");
      if (content.includes("[mcp_servers.patchcord]")) {
        console.log(`\n  ${yellow}⚠ Codex CLI already configured in this project${r}`);
        console.log(`  ${dim}${configPath}${r}`);
        const replace = (await ask(`  ${dim}Replace? (y/N):${r} `)).trim().toLowerCase();
        if (replace !== "y" && replace !== "yes") {
          console.log("Keeping existing config.");
          rl.close();
          process.exit(0);
        }
      }
    }
  }

  let token = "";
  let identity = "";
  let serverUrl = "https://mcp.patchcord.dev";

  console.log(`\n${dim}Get your token at:${r} ${cyan}https://patchcord.dev/console${r}`);
  console.log(`${dim}Create a project → Add agent → Copy token${r}`);

  while (!identity) {
    token = (await ask(`\n${bold}Paste your agent token:${r} `)).trim();

    if (!token) {
      console.error("Token is required. Get one from your patchcord dashboard.");
      rl.close();
      process.exit(1);
    }

    console.log("Validating...");
    const validateResp = run(`curl -sf --max-time 5 -H "Authorization: Bearer ${token}" "${serverUrl}/api/inbox?limit=0"`);
    if (validateResp) {
      try {
        const data = JSON.parse(validateResp);
        identity = `${data.agent_id}@${data.namespace_id}`;
        console.log(`  ${green}✓${r} ${bold}${identity}${r}`);
      } catch {}
    }
    if (!identity) {
      console.log(`  ${red}✗${r} Token not recognized`);
      const retry = (await ask(`${dim}Try again? (Y/n):${r} `)).trim().toLowerCase();
      if (retry === "n" || retry === "no") {
        rl.close();
        process.exit(1);
      }
    }
  }

  const customUrl = (await ask(`\n${dim}Custom server URL? (y/N):${r} `)).trim().toLowerCase();
  if (customUrl === "y" || customUrl === "yes") {
    const url = (await ask("Server URL: ")).trim();
    if (url) serverUrl = url;

    // Re-validate against custom server if identity wasn't found
    if (!identity) {
      console.log("Validating token...");
      const resp2 = run(`curl -sf --max-time 5 -H "Authorization: Bearer ${token}" "${serverUrl}/api/inbox?limit=0"`);
      if (resp2) {
        try {
          const data = JSON.parse(resp2);
          identity = `${data.agent_id}@${data.namespace_id}`;
          console.log(`  ${green}✓${r} ${bold}${identity}${r}`);
        } catch {}
      }
    }
  }

  rl.close();

  if (isCursor) {
    // Cursor: write .cursor/mcp.json (per-project)
    const cursorDir = join(cwd, ".cursor");
    mkdirSync(cursorDir, { recursive: true });
    const cursorPath = join(cursorDir, "mcp.json");
    const cursorConfig = {
      mcpServers: {
        patchcord: {
          command: "npx",
          args: [
            "-y", "mcp-remote",
            serverUrl,
            "--header",
            `Authorization: Bearer ${token}`,
          ],
        },
      },
    };

    if (existsSync(cursorPath)) {
      try {
        const existing = JSON.parse(readFileSync(cursorPath, "utf-8"));
        existing.mcpServers = existing.mcpServers || {};
        existing.mcpServers.patchcord = cursorConfig.mcpServers.patchcord;
        writeFileSync(cursorPath, JSON.stringify(existing, null, 2) + "\n");
      } catch {
        writeFileSync(cursorPath, JSON.stringify(cursorConfig, null, 2) + "\n");
      }
    } else {
      writeFileSync(cursorPath, JSON.stringify(cursorConfig, null, 2) + "\n");
    }
    console.log(`\n  ${green}✓${r} Cursor configured: ${dim}${cursorPath}${r}`);
    console.log(`  ${dim}Per-project only — other projects won't see this agent.${r}`);
  } else if (isWindsurf) {
    // Windsurf: global only (~/.codeium/windsurf/mcp_config.json)
    const wsPath = join(process.env.HOME || "", ".codeium", "windsurf", "mcp_config.json");
    const wsConfig = {
      mcpServers: {
        patchcord: {
          command: "npx",
          args: [
            "-y", "mcp-remote",
            serverUrl,
            "--header",
            `Authorization: Bearer ${token}`,
          ],
        },
      },
    };

    if (existsSync(wsPath)) {
      try {
        const content = readFileSync(wsPath, "utf-8").trim();
        const existing = content ? JSON.parse(content) : {};
        existing.mcpServers = existing.mcpServers || {};
        existing.mcpServers.patchcord = wsConfig.mcpServers.patchcord;
        writeFileSync(wsPath, JSON.stringify(existing, null, 2) + "\n");
      } catch {
        writeFileSync(wsPath, JSON.stringify(wsConfig, null, 2) + "\n");
      }
    } else {
      mkdirSync(join(process.env.HOME || "", ".codeium", "windsurf"), { recursive: true });
      writeFileSync(wsPath, JSON.stringify(wsConfig, null, 2) + "\n");
    }
    console.log(`\n  ${green}✓${r} Windsurf configured: ${dim}${wsPath}${r}`);
    console.log(`  ${yellow}Global config — all Windsurf projects share this agent.${r}`);
    console.log(`  ${dim}Windsurf does not support per-project MCP configs.${r}`);
  } else if (isCodex) {
    // Codex: copy skill + write config
    const dest = join(cwd, ".agents", "skills", "patchcord");
    mkdirSync(dest, { recursive: true });
    cpSync(join(pluginRoot, "skills", "inbox", "SKILL.md"), join(dest, "SKILL.md"));

    const codexDir = join(cwd, ".codex");
    mkdirSync(codexDir, { recursive: true });
    const configPath = join(codexDir, "config.toml");
    let existing = existsSync(configPath) ? readFileSync(configPath, "utf-8") : "";
    if (!existing.includes("[mcp_servers.patchcord]")) {
      existing = existing.trimEnd() + `\n\n[mcp_servers.patchcord]\nurl = "${serverUrl}/mcp/bearer"\nhttp_headers = { "Authorization" = "Bearer ${token}", "X-Patchcord-Client-Type" = "codex" }\n`;
      writeFileSync(configPath, existing);
    }
    console.log(`\n  ${green}✓${r} Codex configured: ${dim}${configPath}${r}`);
    console.log(`  ${green}✓${r} Skill installed`);
  } else {
    // Claude Code: write .mcp.json
    const mcpPath = join(cwd, ".mcp.json");
    const mcpConfig = {
      mcpServers: {
        patchcord: {
          type: "http",
          url: `${serverUrl}/mcp`,
          headers: {
            Authorization: `Bearer ${token}`,
          },
        },
      },
    };

    if (existsSync(mcpPath)) {
      try {
        const existing = JSON.parse(readFileSync(mcpPath, "utf-8"));
        existing.mcpServers = existing.mcpServers || {};
        existing.mcpServers.patchcord = mcpConfig.mcpServers.patchcord;
        writeFileSync(mcpPath, JSON.stringify(existing, null, 2) + "\n");
      } catch {
        writeFileSync(mcpPath, JSON.stringify(mcpConfig, null, 2) + "\n");
      }
    } else {
      writeFileSync(mcpPath, JSON.stringify(mcpConfig, null, 2) + "\n");
    }
    console.log(`\n  ${green}✓${r} Claude Code configured: ${dim}${mcpPath}${r}`);
  }

  const toolName = isWindsurf ? "Windsurf" : isCursor ? "Cursor" : isCodex ? "Codex" : "Claude Code";
  console.log(`\n${dim}Restart your ${toolName} session, then run:${r} ${bold}inbox()${r}`);
  process.exit(0);
}

// ── back-compat: init → install + agent ───────────────────────
if (cmd === "init") {
  console.log(`"patchcord init" is now two commands:

  patchcord install    One-time global setup (once)
  patchcord agent      Set up MCP for this project (per project)`);
  process.exit(0);
}

// ── skill: custom skill from web console ─────────────────────
if (cmd === "skill") {
  const sub = process.argv[3];
  const cwd = process.cwd();

  // Find .mcp.json to get URL and token
  let mcpJson = null;
  let dir = cwd;
  while (dir && dir !== "/") {
    const p = join(dir, ".mcp.json");
    if (existsSync(p)) { mcpJson = p; break; }
    dir = dirname(dir);
  }

  if (!mcpJson) {
    console.error("No .mcp.json found. Run 'patchcord agent' first.");
    process.exit(1);
  }

  const { readFileSync, writeFileSync } = await import("fs");
  const config = JSON.parse(readFileSync(mcpJson, "utf-8"));
  const mcpUrl = config?.mcpServers?.patchcord?.url || "";
  const auth = config?.mcpServers?.patchcord?.headers?.Authorization || "";
  const baseUrl = mcpUrl.replace(/\/mcp(\/bearer)?$/, "");
  const token = auth.replace(/^Bearer\s+/, "");

  if (!baseUrl || !token) {
    console.error("Cannot read patchcord URL/token from .mcp.json");
    process.exit(1);
  }

  // Derive namespace and agent from the token by calling /api/inbox
  let namespace = "", agentId = "";
  try {
    const resp = run(`curl -s -H "Authorization: Bearer ${token}" "${baseUrl}/api/inbox?limit=0"`);
    if (resp) {
      const data = JSON.parse(resp);
      namespace = data.namespace_id || "";
      agentId = data.agent_id || "";
    }
  } catch {}

  if (!namespace || !agentId) {
    console.error("Cannot determine agent identity. Check your token.");
    process.exit(1);
  }

  const skillDir = join(cwd, ".claude", "skills", "patchcord-custom");
  const skillFile = join(skillDir, "SKILL.md");

  if (sub === "apply" || !sub) {
    console.log(`Fetching custom skill for ${namespace}:${agentId}...`);
    const resp = run(`curl -s -H "Authorization: Bearer ${token}" "${baseUrl}/api/skills/${namespace}/${agentId}"`);
    if (!resp) {
      console.log("No custom skill found or server unreachable.");
      process.exit(0);
    }
    try {
      const data = JSON.parse(resp);
      if (data.skill_text) {
        mkdirSync(skillDir, { recursive: true });
        writeFileSync(skillFile, data.skill_text.trim() + "\n");
        console.log(`✓ Custom skill applied to ${skillFile}`);
      } else {
        console.log("No custom skill set for this agent.");
      }
    } catch {
      console.error("Failed to parse skill response.");
      process.exit(1);
    }
  } else {
    console.log(`Usage: patchcord skill apply`);
  }

  // Clean up old PATCHCORD.md if it exists
  const oldFile = join(cwd, "PATCHCORD.md");
  if (existsSync(oldFile)) {
    const { unlinkSync } = await import("fs");
    unlinkSync(oldFile);
  }
  process.exit(0);
}

console.error(`Unknown command: ${cmd}. Run 'patchcord help' for usage.`);
process.exit(1);
