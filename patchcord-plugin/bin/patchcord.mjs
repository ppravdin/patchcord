#!/usr/bin/env node

import { existsSync, mkdirSync, cpSync, readdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { execSync } from "child_process";
import { homedir } from "os";

const HOME = homedir();

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

function isSafeToken(t) {
  return /^[A-Za-z0-9_\-=+/.]+$/.test(t) && t.length < 200;
}

function isSafeUrl(u) {
  try {
    const parsed = new URL(u);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch { return false; }
}

function isSafeId(s) {
  return /^[A-Za-z0-9_\-]+$/.test(s) && s.length < 100;
}

const PROJECT_MARKERS = [
  ".git", "package.json", "package-lock.json", "Cargo.toml", "go.mod", "go.sum",
  "pyproject.toml", "pom.xml", "build.gradle", "Makefile", "CMakeLists.txt",
  "Gemfile", "composer.json", "mix.exs", "requirements.txt", "setup.py",
  ".claude", ".codex", ".cursor", ".vscode", ".openclaw",
];

function detectFolder(dir) {
  if (dir === HOME || dir === HOME + "/" || dir === "/") return "HOME";
  for (const m of PROJECT_MARKERS) {
    if (existsSync(join(dir, m))) return "PROJECT";
  }
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch { return "UNKNOWN"; }
  if (entries.length === 0) return "EMPTY";
  const files = entries.filter(e => e.isFile());
  const dirs = entries.filter(e => e.isDirectory());
  if (files.length === 0 && dirs.length >= 2) return "CONTAINER";
  return "UNKNOWN";
}


if (cmd === "help" || cmd === "--help" || cmd === "-h") {
  console.log(`patchcord — agent messaging for AI coding agents

Usage:
  npx patchcord@latest                                    Setup via browser (patchcord.dev)
  npx patchcord@latest --token <token>                    Self-hosted / CI setup
  npx patchcord@latest --token <token> --server <url>     Self-hosted with custom server
  npx patchcord@latest --full                             Same + full statusline
  npx patchcord@latest skill apply                        Fetch custom skill from web console`);
  process.exit(0);
}

if (cmd === "plugin-path") {
  console.log(pluginRoot);
  process.exit(0);
}

// ── main flow: global setup + project setup (or just install/agent for back-compat) ──
if (!cmd || cmd === "install" || cmd === "agent" || cmd === "--token" || cmd === "--no-browser" || cmd === "--server") {
  const flags = cmd?.startsWith("--") ? process.argv.slice(2) : process.argv.slice(3);
  const fullStatusline = flags.includes("--full");
  const { readFileSync, writeFileSync, unlinkSync } = await import("fs");

  function safeReadJson(filePath) {
    try {
      let content = readFileSync(filePath, "utf-8");
      // Strip JSONC comments (Zed, Gemini use JSONC)
      content = content.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
      content = content.replace(/,\s*([}\]])/g, "$1");
      return JSON.parse(content);
    } catch { return null; }
  }

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
    // Always re-add marketplace (copies fresh files from this npx package)
    // and install/update plugin. Claude Code's built-in plugin update
    // doesn't detect new versions from local sources (#37252).
    run(`claude plugin marketplace add "${pluginRoot}"`);
    const installed = run(`claude plugin list`)?.includes("patchcord");
    if (installed) {
      run(`claude plugin update patchcord@patchcord-marketplace`);
      globalChanges.push("Claude Code plugin updated");
    } else {
      run(`claude plugin install patchcord@patchcord-marketplace`);
      globalChanges.push("Claude Code plugin installed");
    }

    const claudeSettings = join(HOME, ".claude", "settings.json");
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
  const cursorSkillsRoot = join(HOME, ".cursor", "skills-cursor");
  if (existsSync(cursorSkillsRoot)) {
    const cursorSkillDir = join(cursorSkillsRoot, "patchcord");
    const cursorWaitDir = join(cursorSkillsRoot, "patchcord-wait");
    let cursorChanged = false;
    if (!existsSync(cursorSkillDir)) {
      mkdirSync(cursorSkillDir, { recursive: true });
      cpSync(join(pluginRoot, "skills", "patchcord", "SKILL.md"), join(cursorSkillDir, "SKILL.md"));
      cursorChanged = true;
    }
    if (!existsSync(cursorWaitDir)) {
      mkdirSync(cursorWaitDir, { recursive: true });
      cpSync(join(pluginRoot, "skills", "patchcord-wait", "SKILL.md"), join(cursorWaitDir, "SKILL.md"));
      cursorChanged = true;
    }
    if (cursorChanged) globalChanges.push("Cursor skills installed");
  }

  // Windsurf
  if (existsSync(join(HOME, ".codeium", "windsurf"))) {
    const windsurfSkillDir = join(HOME, ".codeium", "windsurf", "skills", "patchcord");
    const windsurfWaitDir = join(HOME, ".codeium", "windsurf", "skills", "patchcord-wait");
    let windsurfChanged = false;
    if (!existsSync(windsurfSkillDir)) {
      mkdirSync(windsurfSkillDir, { recursive: true });
      cpSync(join(pluginRoot, "skills", "patchcord", "SKILL.md"), join(windsurfSkillDir, "SKILL.md"));
      windsurfChanged = true;
    }
    if (!existsSync(windsurfWaitDir)) {
      mkdirSync(windsurfWaitDir, { recursive: true });
      cpSync(join(pluginRoot, "skills", "patchcord-wait", "SKILL.md"), join(windsurfWaitDir, "SKILL.md"));
      windsurfChanged = true;
    }
    if (windsurfChanged) globalChanges.push("Windsurf skills installed");
  }

  // Gemini CLI
  if (existsSync(join(HOME, ".gemini"))) {
    const geminiSkillDir = join(HOME, ".gemini", "skills", "patchcord");
    const geminiWaitDir = join(HOME, ".gemini", "skills", "patchcord-wait");
    const geminiCmdDir = join(HOME, ".gemini", "commands");
    let geminiChanged = false;
    if (!existsSync(geminiSkillDir)) {
      mkdirSync(geminiSkillDir, { recursive: true });
      cpSync(join(pluginRoot, "skills", "patchcord", "SKILL.md"), join(geminiSkillDir, "SKILL.md"));
      geminiChanged = true;
    }
    if (!existsSync(geminiWaitDir)) {
      mkdirSync(geminiWaitDir, { recursive: true });
      cpSync(join(pluginRoot, "skills", "patchcord-wait", "SKILL.md"), join(geminiWaitDir, "SKILL.md"));
      geminiChanged = true;
    }
    if (!existsSync(join(geminiCmdDir, "patchcord.toml"))) {
      mkdirSync(geminiCmdDir, { recursive: true });
      cpSync(join(pluginRoot, "commands", "patchcord.toml"), join(geminiCmdDir, "patchcord.toml"));
      cpSync(join(pluginRoot, "commands", "patchcord-wait.toml"), join(geminiCmdDir, "patchcord-wait.toml"));
      geminiChanged = true;
    }
    if (geminiChanged) globalChanges.push("Gemini CLI skills + commands installed");
  }

  // Codex CLI — clean up old apps.patchcord setting (it blocks the plugin)
  const codexConfig = join(HOME, ".codex", "config.toml");
  if (existsSync(codexConfig)) {
    const content = readFileSync(codexConfig, "utf-8");
    if (content.includes("[apps.patchcord]")) {
      const cleaned = content.replace(/\[apps\.patchcord\]\n(?:(?!\[)[^\n]*\n?)*/g, "").replace(/\n{3,}/g, "\n\n").trim();
      writeFileSync(codexConfig, cleaned + "\n");
      globalChanges.push("Removed old apps.patchcord setting");
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

  // Tool picker only shown for --token bypass. Browser flow gets tool from web.
  let choice = "";

  const CLIENT_TYPE_MAP = {
    "claude_code": "1", "codex": "2", "cursor": "3", "windsurf": "4",
    "gemini": "5", "vscode": "6", "zed": "7", "opencode": "8", "openclaw": "9", "antigravity": "10",
    "cline": "11",
  };



  let token = "";
  let identity = "";
  let serverUrl = "https://mcp.patchcord.dev";
  let apiUrl = "https://api.patchcord.dev";
  let clientType = "";

  // --server flag for self-hosters
  const serverFlag = flags.find(f => f.startsWith("--server="))?.split("=")[1]
    || (flags.includes("--server") ? flags[flags.indexOf("--server") + 1] : "");
  if (serverFlag) {
    if (!isSafeUrl(serverFlag)) {
      console.error("Invalid server URL. Must start with https:// or http://");
      rl.close();
      process.exit(1);
    }
    serverUrl = serverFlag.replace(/\/+$/, "");
    apiUrl = serverUrl;
  }

  // --token bypass for power users / CI / self-hosters
  const tokenFlag = flags.find(f => f.startsWith("--token="))?.split("=")[1]
    || (flags.includes("--token") ? flags[flags.indexOf("--token") + 1] : "");

  if (tokenFlag) {
    // --token bypass: need tool picker in terminal
    console.log(`\n${bold}Which tool are you setting up?${r}\n`);
    console.log(`  ${cyan}1.${r} Claude Code   ${cyan}5.${r} Gemini CLI`);
    console.log(`  ${cyan}2.${r} Codex CLI     ${cyan}6.${r} VS Code`);
    console.log(`  ${cyan}3.${r} Cursor        ${cyan}7.${r} Zed`);
    console.log(`  ${cyan}4.${r} Windsurf      ${cyan}8.${r} OpenCode`);
    console.log(`  ${cyan}11.${r} Cline         ${cyan}9.${r} OpenClaw\n`);
    choice = (await ask(`${dim}Choose (1-9, 11):${r} `)).trim();
    if (!["1","2","3","4","5","6","7","8","9","11"].includes(choice)) {
      console.error("Invalid choice.");
      rl.close();
      process.exit(1);
    }
    token = tokenFlag.trim();
    if (!isSafeToken(token)) {
      console.error("Invalid token format.");
      rl.close();
      process.exit(1);
    }
    console.log("Validating token...");
    const validateResp = run(`curl -sf --max-time 5 -H "Authorization: Bearer ${token}" "${serverUrl}/api/inbox?limit=0"`);
    if (validateResp) {
      try {
        const data = JSON.parse(validateResp);
        identity = `${data.agent_id}@${data.namespace_id}`;
        console.log(`  ${green}✓${r} ${bold}${identity}${r}`);
      } catch {}
    }
    if (!identity) {
      console.error("Token not recognized.");
      rl.close();
      process.exit(1);
    }
    rl.close();
  } else {
    // Check if patchcord is already configured — offer to update URL without re-auth
    let existingToken = "";
    let existingConfigFile = "";
    const mcpJsonPath = join(cwd, ".mcp.json");
    const codexTomlPath = join(cwd, ".codex", "config.toml");
    if (existsSync(mcpJsonPath)) {
      try {
        const existing = JSON.parse(readFileSync(mcpJsonPath, "utf-8"));
        const pt = existing?.mcpServers?.patchcord;
        if (pt?.headers?.Authorization) {
          existingToken = pt.headers.Authorization.replace(/^Bearer\s+/i, "");
          existingConfigFile = mcpJsonPath;
        }
      } catch {}
    }
    if (!existingToken && existsSync(codexTomlPath)) {
      try {
        const content = readFileSync(codexTomlPath, "utf-8");
        const match = content.match(/Bearer\s+([^\s"]+)/);
        if (match) {
          existingToken = match[1];
          existingConfigFile = codexTomlPath;
        }
      } catch {}
    }
    // Global configs (Antigravity, OpenClaw, Gemini, Windsurf, Zed) are NOT
    // checked here. They're set up once globally and should not block new
    // project setup. Only per-project configs trigger "already configured."
    if (existingToken) {
      // Figure out which tool is already configured
      const existingToolName = existingConfigFile.includes(".codex") ? "Codex"
        : existingConfigFile.includes("antigravity") ? "Antigravity"
        : existingConfigFile.includes("openclaw") ? "OpenClaw"
        : existingConfigFile.includes(".cursor") ? "Cursor"
        : existingConfigFile.includes(".vscode") ? "VS Code"
        : "Claude Code";

      // Validate the existing token to get identity
      let existingIdentity = "";
      const validateResp = run(`curl -sf --max-time 5 -H "Authorization: Bearer ${existingToken}" "${serverUrl}/api/inbox?limit=0&count_only=1"`);
      if (validateResp) {
        try {
          const data = JSON.parse(validateResp);
          existingIdentity = `${data.agent_id}@${data.namespace_id}`;
        } catch {}
      }

      const identityStr = existingIdentity ? ` (${bold}${existingIdentity}${r}${dim})` : "";
      console.log(`\n  ${dim}${existingToolName} agent is already configured in this project${identityStr}${r}`);

      if (rl) rl.close();
      const { createInterface: createRLU } = await import("readline");
      const rlU = createRLU({ input: process.stdin, output: process.stdout });
      const askU = (q) => new Promise((resolve) => rlU.question(q, resolve));

      // Q1: Add another agent? (most likely reason to re-run installer)
      const addAnswer = (await askU(`  ${bold}Add another agent to this project? (y/N):${r} `)).trim().toLowerCase();
      if (addAnswer === "y" || addAnswer === "yes") {
        rlU.close();
        // Drop into browser connect flow — fresh setup for new agent
        token = "";
      } else {
        // Q2: Update existing agent?
        const updateAnswer = (await askU(`  ${bold}Update ${existingToolName} agent? (y/N):${r} `)).trim().toLowerCase();
        rlU.close();
        if (updateAnswer === "y" || updateAnswer === "yes") {
          token = existingToken;
          if (existingIdentity) {
            identity = existingIdentity;
            const vResp = validateResp ? JSON.parse(validateResp) : {};
            clientType = vResp.client_type || "";
            choice = CLIENT_TYPE_MAP[clientType] || "";
            console.log(`  ${green}✓${r} ${bold}${identity}${r} — token valid`);
          } else {
            console.log(`  ${yellow}⚠${r} Token expired or invalid. Starting fresh setup.`);
            token = "";
          }
        } else {
          // Both N — skills already updated by global setup, done
          console.log(`\n  ${dim}Skills updated.${r}`);
          process.exit(0);
        }
      }
    }

    if (!token) {
    // Browser connect flow
    if (rl) rl.close();

    function canOpenBrowser() {
      if (process.env.SSH_CLIENT || process.env.SSH_TTY) return false;
      if (!process.env.DISPLAY && process.platform === "linux") return false;
      if (flags.includes("--no-browser")) return false;
      return true;
    }

    function openBrowser(url) {
      try {
        if (process.platform === "darwin") execSync(`open "${url}"`, { stdio: "ignore" });
        else if (process.platform === "win32") execSync(`start "" "${url}"`, { stdio: "ignore" });
        else execSync(`xdg-open "${url}"`, { stdio: "ignore" });
        return true;
      } catch { return false; }
    }

    // Create session
    let sessionId = "";
    try {
      const resp = run(`curl -sf --max-time 10 -X POST "${apiUrl}/api/connect/session" -H "Content-Type: application/json" -d '{"tool":"${choice}"}'`);
      if (resp) {
        const data = JSON.parse(resp);
        sessionId = data.session_id || "";
      }
    } catch {}

    if (!sessionId) {
      // Fallback to manual token paste if connect API unavailable
      console.log(`\n${dim}Browser connect unavailable. Paste token manually.${r}`);
      console.log(`${dim}Get your token at:${r} ${cyan}https://patchcord.dev/console${r}`);
      const { createInterface: createRL2 } = await import("readline");
      const rl2 = createRL2({ input: process.stdin, output: process.stdout });
      const ask2 = (q) => new Promise((resolve) => rl2.question(q, resolve));
      token = (await ask2(`\n${bold}Paste your agent token:${r} `)).trim();
      rl2.close();
      if (!token || !isSafeToken(token)) {
        console.error("Invalid token.");
        process.exit(1);
      }
      const validateResp = run(`curl -sf --max-time 5 -H "Authorization: Bearer ${token}" "${serverUrl}/api/inbox?limit=0"`);
      if (validateResp) {
        try {
          const data = JSON.parse(validateResp);
          identity = `${data.agent_id}@${data.namespace_id}`;
          console.log(`  ${green}✓${r} ${bold}${identity}${r}`);
        } catch {}
      }
      if (!identity) {
        console.error("Token not recognized.");
        process.exit(1);
      }
    } else {
      // Open browser or show URL
      const connectUrl = `https://patchcord.dev/connect?session=${sessionId}`;

      if (canOpenBrowser()) {
        const opened = openBrowser(connectUrl);
        if (opened) {
          console.log(`\n  ${green}✓${r} Browser opened.`);
        } else {
          console.log(`\n  ${dim}Could not open browser. Open this URL manually:${r}`);
          console.log(`\n  ${cyan}${connectUrl}${r}\n`);
        }
      } else {
        console.log(`\n  ${dim}Can't open a browser on this machine.${r}`);
        console.log(`  ${dim}Open this URL on any device:${r}`);
        console.log(`\n  ${cyan}${connectUrl}${r}\n`);
      }

      console.log(`  ${dim}⏳ Waiting for you to complete setup in the browser...${r}`);
      console.log(`  ${dim}   (press Ctrl+C to cancel)${r}\n`);

      // SSE listener — wait for session completion
      const http = await import("https");
      const sseResult = await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          reject(new Error("Session expired. Run npx patchcord@latest again."));
        }, 15 * 60 * 1000);

        function connect() {
          const req = http.get(`${apiUrl}/api/connect/session/${sessionId}/wait`, {
            headers: { "Accept": "text/event-stream" },
          }, (res) => {
            if (res.statusCode !== 200) {
              clearTimeout(timeout);
              reject(new Error(`Server returned ${res.statusCode}`));
              return;
            }
            let buffer = "";
            res.on("data", (chunk) => {
              buffer += chunk.toString();
              const lines = buffer.split("\n");
              buffer = lines.pop();
              for (const line of lines) {
                if (line.startsWith("data: ")) {
                  try {
                    const payload = JSON.parse(line.slice(6));
                    if (payload.error) {
                      clearTimeout(timeout);
                      reject(new Error(payload.error));
                      return;
                    }
                    if (payload.token) {
                      clearTimeout(timeout);
                      resolve(payload);
                      return;
                    }
                  } catch {}
                }
              }
            });
            res.on("end", () => {
              // Connection dropped — retry
              setTimeout(connect, 2000);
            });
            res.on("error", () => {
              setTimeout(connect, 2000);
            });
          });
          req.on("error", () => {
            setTimeout(connect, 2000);
          });
        }

        connect();
      });

      token = sseResult.token;
      identity = `${sseResult.agent_id}@${sseResult.namespace_id}`;
      clientType = sseResult.client_type || sseResult.tool || "";
      choice = CLIENT_TYPE_MAP[clientType] || "";
      console.log(`  ${green}✓${r} ${bold}${identity}${r} connected.`);

      if (!choice) {
        // Web UI didn't send client_type — default to Claude Code
        choice = "1";
      }
    }
  } // end connect flow
  } // end if (!token)

  const isCodex = choice === "2";
  const isCursor = choice === "3";
  const isWindsurf = choice === "4";
  const isGemini = choice === "5";
  const isVSCode = choice === "6";
  const isZed = choice === "7";
  const isOpenCode = choice === "8";
  const isOpenClaw = choice === "9";
  const isAntigravity = choice === "10";
  const isCline = choice === "11";

  const hostname = run("hostname -s") || run("hostname") || "unknown";

  if (isCursor) {
    // Cursor: write .cursor/mcp.json (per-project)
    const cursorDir = join(cwd, ".cursor");
    mkdirSync(cursorDir, { recursive: true });
    const cursorPath = join(cursorDir, "mcp.json");
    const cursorConfig = {
      mcpServers: {
        patchcord: {
          url: `${serverUrl}/mcp`,
          headers: {
            Authorization: `Bearer ${token}`,
            "X-Patchcord-Machine": hostname,
          },
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
    const wsPath = join(HOME, ".codeium", "windsurf", "mcp_config.json");
    const wsConfig = {
      mcpServers: {
        patchcord: {
          url: `${serverUrl}/mcp`,
          headers: {
            Authorization: `Bearer ${token}`,
            "X-Patchcord-Machine": hostname,
          },
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
      mkdirSync(join(HOME, ".codeium", "windsurf"), { recursive: true });
      writeFileSync(wsPath, JSON.stringify(wsConfig, null, 2) + "\n");
    }
    console.log(`\n  ${green}✓${r} Windsurf configured: ${dim}${wsPath}${r}`);
    console.log(`  ${yellow}Global config — all Windsurf projects share this agent.${r}`);
  } else if (isGemini) {
    // Gemini CLI: global only (~/.gemini/settings.json)
    const geminiPath = join(HOME, ".gemini", "settings.json");
    let geminiSettings = (existsSync(geminiPath) && safeReadJson(geminiPath)) || {};
    if (!geminiSettings.mcpServers) geminiSettings.mcpServers = {};
    geminiSettings.mcpServers.patchcord = {
      httpUrl: `${serverUrl}/mcp`,
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Patchcord-Machine": hostname,
      },
    };
    // Clean up deprecated tools.allowed if present (removed in Gemini CLI 1.0)
    if (geminiSettings.tools?.allowed) {
      geminiSettings.tools.allowed = geminiSettings.tools.allowed.filter(t => !t.startsWith("mcp_patchcord_"));
      if (geminiSettings.tools.allowed.length === 0) delete geminiSettings.tools;
    }
    mkdirSync(join(HOME, ".gemini"), { recursive: true });
    writeFileSync(geminiPath, JSON.stringify(geminiSettings, null, 2) + "\n");
    console.log(`\n  ${green}✓${r} Gemini CLI configured: ${dim}${geminiPath}${r}`);
    console.log(`  ${yellow}Global config — all Gemini CLI projects share this agent.${r}`);
  } else if (isZed) {
    // Zed: global settings.json → context_servers
    const zedPath = process.platform === "darwin"
      ? join(HOME, "Library", "Application Support", "Zed", "settings.json")
      : join(HOME, ".config", "zed", "settings.json");
    let zedSettings = (existsSync(zedPath) && safeReadJson(zedPath)) || {};
    if (!zedSettings.context_servers) zedSettings.context_servers = {};
    zedSettings.context_servers.patchcord = {
      url: `${serverUrl}/mcp`,
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Patchcord-Machine": hostname,
      },
    };
    const zedDir = process.platform === "darwin"
      ? join(HOME, "Library", "Application Support", "Zed")
      : join(HOME, ".config", "zed");
    mkdirSync(zedDir, { recursive: true });
    writeFileSync(zedPath, JSON.stringify(zedSettings, null, 2) + "\n");
    console.log(`\n  ${green}✓${r} Zed configured: ${dim}${zedPath}${r}`);
    console.log(`  ${yellow}Global config — all Zed projects share this agent.${r}`);
  } else if (isOpenCode) {
    // OpenCode: per-project opencode.json → mcp
    const ocPath = join(cwd, "opencode.json");
    let ocConfig = {};
    if (existsSync(ocPath)) {
      try {
        ocConfig = JSON.parse(readFileSync(ocPath, "utf-8"));
      } catch {}
    }
    if (!ocConfig.mcp) ocConfig.mcp = {};
    ocConfig.mcp.patchcord = {
      type: "remote",
      url: `${serverUrl}/mcp`,
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Patchcord-Machine": hostname,
      },
    };
    writeFileSync(ocPath, JSON.stringify(ocConfig, null, 2) + "\n");
    console.log(`\n  ${green}✓${r} OpenCode configured: ${dim}${ocPath}${r}`);
  } else if (isOpenClaw) {
    // OpenClaw: global ~/.openclaw/openclaw.json → mcp.servers
    // Try CLI first, fall back to direct file write
    const openclawServerEntry = JSON.stringify({
      url: `${serverUrl}/mcp`,
      transport: "streamable-http",
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Patchcord-Machine": hostname,
      },
      connectionTimeoutMs: 300000,
    });
    const cliResult = run(`openclaw mcp set patchcord '${openclawServerEntry.replace(/'/g, "'\\''")}'`);
    if (cliResult !== null) {
      console.log(`\n  ${green}✓${r} OpenClaw configured via CLI: ${dim}openclaw mcp set${r}`);
    } else {
      // CLI not available — write config directly
      const openclawDir = join(HOME, ".openclaw");
      const openclawPath = join(openclawDir, "openclaw.json");
      let openclawConfig = {};
      if (existsSync(openclawPath)) {
        try {
          openclawConfig = JSON.parse(readFileSync(openclawPath, "utf-8"));
        } catch {}
      }
      if (!openclawConfig.mcp) openclawConfig.mcp = {};
      if (!openclawConfig.mcp.servers) openclawConfig.mcp.servers = {};
      openclawConfig.mcp.servers.patchcord = {
        url: `${serverUrl}/mcp`,
        transport: "streamable-http",
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Patchcord-Machine": hostname,
        },
        connectionTimeoutMs: 300000,
      };
      mkdirSync(openclawDir, { recursive: true });
      writeFileSync(openclawPath, JSON.stringify(openclawConfig, null, 2) + "\n");
      console.log(`\n  ${green}✓${r} OpenClaw configured: ${dim}${openclawPath}${r}`);
    }
    console.log(`  ${yellow}Global config — all OpenClaw channels share this agent.${r}`);
    console.log(`  ${dim}Run: openclaw gateway restart${r}`);
    // mcp-remote fallback note for older OpenClaw versions
    console.log(`\n  ${dim}If tools don't appear after restart, your OpenClaw may be too old${r}`);
    console.log(`  ${dim}for streamable-http. Update to v2026.3.31+ or use mcp-remote:${r}`);
    console.log(`  ${dim}openclaw mcp set patchcord '{"command":"npx","args":["mcp-remote","${serverUrl}/mcp","--header","Authorization: Bearer ${token}"],"transport":"stdio"}'${r}`);
  } else if (isAntigravity) {
    // Antigravity: global ~/.gemini/antigravity/mcp_config.json → mcpServers
    const agDir = join(HOME, ".gemini", "antigravity");
    const agPath = join(agDir, "mcp_config.json");
    let agConfig = {};
    if (existsSync(agPath)) {
      try {
        agConfig = JSON.parse(readFileSync(agPath, "utf-8"));
      } catch {}
    }
    if (!agConfig.mcpServers) agConfig.mcpServers = {};
    agConfig.mcpServers.patchcord = {
      serverUrl: `${serverUrl}/mcp`,
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Patchcord-Machine": hostname,
      },
    };
    mkdirSync(agDir, { recursive: true });
    writeFileSync(agPath, JSON.stringify(agConfig, null, 2) + "\n");
    console.log(`\n  ${green}✓${r} Antigravity configured: ${dim}${agPath}${r}`);
    // Install global skills
    const agSkillDir = join(agDir, "skills", "patchcord");
    const agWaitDir = join(agDir, "skills", "patchcord-wait");
    mkdirSync(agSkillDir, { recursive: true });
    mkdirSync(agWaitDir, { recursive: true });
    cpSync(join(pluginRoot, "skills", "patchcord", "SKILL.md"), join(agSkillDir, "SKILL.md"));
    cpSync(join(pluginRoot, "skills", "patchcord-wait", "SKILL.md"), join(agWaitDir, "SKILL.md"));
    console.log(`  ${green}✓${r} Skills installed: ${dim}patchcord${r}, ${dim}patchcord-wait${r}`);
    console.log(`  ${yellow}Global config — all Antigravity projects share this agent.${r}`);
  } else if (isCline) {
    // Cline VS Code extension: global cline_mcp_settings.json
    // Config lives in VS Code's globalStorage for saoudrizwan.claude-dev
    // Try stable VS Code first, then Insiders, then Cursor
    const vsCodeVariants = process.platform === "darwin"
      ? [
          join(HOME, "Library", "Application Support", "Code", "User", "globalStorage"),
          join(HOME, "Library", "Application Support", "Code - Insiders", "User", "globalStorage"),
          join(HOME, "Library", "Application Support", "Cursor", "User", "globalStorage"),
        ]
      : process.platform === "win32"
      ? [
          join(process.env.APPDATA || join(HOME, "AppData", "Roaming"), "Code", "User", "globalStorage"),
          join(process.env.APPDATA || join(HOME, "AppData", "Roaming"), "Code - Insiders", "User", "globalStorage"),
          join(process.env.APPDATA || join(HOME, "AppData", "Roaming"), "Cursor", "User", "globalStorage"),
        ]
      : [
          join(HOME, ".config", "Code", "User", "globalStorage"),
          join(HOME, ".config", "Code - Insiders", "User", "globalStorage"),
          join(HOME, ".config", "Cursor", "User", "globalStorage"),
        ];

    // Find the first variant that has Cline's globalStorage directory (or exists at all)
    const clineExtDir = "saoudrizwan.claude-dev";
    let clineSettingsDir = null;
    for (const base of vsCodeVariants) {
      const candidate = join(base, clineExtDir, "settings");
      const extDir = join(base, clineExtDir);
      if (existsSync(extDir)) {
        clineSettingsDir = candidate;
        break;
      }
    }
    // Fall back to stable VS Code path even if it doesn't exist yet
    if (!clineSettingsDir) {
      const fallbackBase = vsCodeVariants[0];
      clineSettingsDir = join(fallbackBase, clineExtDir, "settings");
    }

    const clinePath = join(clineSettingsDir, "cline_mcp_settings.json");
    let clineConfig = {};
    if (existsSync(clinePath)) {
      try { clineConfig = JSON.parse(readFileSync(clinePath, "utf-8")); } catch {}
    }
    if (!clineConfig.mcpServers) clineConfig.mcpServers = {};
    clineConfig.mcpServers.patchcord = {
      url: `${serverUrl}/mcp`,
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Patchcord-Machine": hostname,
      },
      disabled: false,
      alwaysAllow: [],
    };
    mkdirSync(clineSettingsDir, { recursive: true });
    writeFileSync(clinePath, JSON.stringify(clineConfig, null, 2) + "\n");
    console.log(`\n  ${green}✓${r} Cline configured: ${dim}${clinePath}${r}`);
    console.log(`  ${yellow}Global config — all Cline projects share this agent.${r}`);
  } else if (isVSCode) {
    // VS Code: write .vscode/mcp.json (per-project)
    const vscodeDir = join(cwd, ".vscode");
    mkdirSync(vscodeDir, { recursive: true });
    const vscodePath = join(vscodeDir, "mcp.json");
    const vscodeConfig = {
      servers: {
        patchcord: {
          type: "http",
          url: `${serverUrl}/mcp`,
          headers: {
            Authorization: `Bearer ${token}`,
            "X-Patchcord-Machine": hostname,
          },
        },
      },
    };

    if (existsSync(vscodePath)) {
      try {
        const existing = JSON.parse(readFileSync(vscodePath, "utf-8"));
        existing.servers = existing.servers || {};
        existing.servers.patchcord = vscodeConfig.servers.patchcord;
        writeFileSync(vscodePath, JSON.stringify(existing, null, 2) + "\n");
      } catch {
        writeFileSync(vscodePath, JSON.stringify(vscodeConfig, null, 2) + "\n");
      }
    } else {
      writeFileSync(vscodePath, JSON.stringify(vscodeConfig, null, 2) + "\n");
    }
    console.log(`\n  ${green}✓${r} VS Code configured: ${dim}${vscodePath}${r}`);
    console.log(`  ${dim}Requires GitHub Copilot extension with agent mode enabled.${r}`);
  } else if (isCodex) {
    // Codex: write MCP config + per-project skills + global plugin
    // Per-project skills (working @patchcord in Codex v0.117)
    const skillDest = join(cwd, ".agents", "skills", "patchcord");
    mkdirSync(skillDest, { recursive: true });
    writeFileSync(join(skillDest, "SKILL.md"),
      readFileSync(join(pluginRoot, "per-project-skills", "codex", "SKILL.md"), "utf-8"));
    const waitDest = join(cwd, ".agents", "skills", "patchcord-wait");
    mkdirSync(waitDest, { recursive: true });
    writeFileSync(join(waitDest, "SKILL.md"),
      readFileSync(join(pluginRoot, "skills", "patchcord-wait", "SKILL.md"), "utf-8"));

    const codexDir = join(cwd, ".codex");
    mkdirSync(codexDir, { recursive: true });
    const configPath = join(codexDir, "config.toml");
    let existing = existsSync(configPath) ? readFileSync(configPath, "utf-8") : "";
    // Remove old patchcord config block if present
    existing = existing.replace(/\[mcp_servers\.patchcord[-\w]*\]\n(?:(?!\[)[^\n]*\n?)*/g, "").replace(/\n{3,}/g, "\n\n").trim();
    existing = existing.trimEnd() + `\n\n[mcp_servers.patchcord-codex]\nurl = "${serverUrl}/mcp"\nhttp_headers = { "Authorization" = "Bearer ${token}", "X-Patchcord-Machine" = "${hostname}" }\ntool_timeout_sec = 300\n`;
    writeFileSync(configPath, existing);
    // Clean up any PATCHCORD_TOKEN we previously wrote to .env
    const envPath = join(cwd, ".env");
    if (existsSync(envPath)) {
      const envContent = readFileSync(envPath, "utf-8");
      if (envContent.includes("PATCHCORD_TOKEN=")) {
        const cleaned = envContent.replace(/^PATCHCORD_TOKEN=.*\n?/gm, "").replace(/\n{3,}/g, "\n\n").trim();
        writeFileSync(envPath, cleaned ? cleaned + "\n" : "");
        console.log(`  ${green}✓${r} Cleaned PATCHCORD_TOKEN from .env`);
      }
    }
    // Clean up old per-project slash commands (deprecated in Codex v0.117+)
    for (const dir of [join(codexDir, "prompts"), join(homedir(), ".codex", "prompts")]) {
      for (const f of ["patchcord.md", "patchcord-wait.md"]) {
        const p = join(dir, f);
        if (existsSync(p)) { unlinkSync(p); }
      }
    }
    // Clean up old per-project skill (now installed as global plugin)
    const oldSkillPath = join(cwd, ".agents", "skills", "patchcord", "SKILL.md");
    if (existsSync(oldSkillPath)) { unlinkSync(oldSkillPath); }

    // Install Codex plugin at ~/.agents/plugins/patchcord/ (personal marketplace)
    const pluginVersion = JSON.parse(readFileSync(join(pluginRoot, "package.json"), "utf-8")).version;
    const marketplaceDir = join(homedir(), ".agents", "plugins");
    const pluginDir = join(marketplaceDir, "patchcord");
    mkdirSync(join(pluginDir, ".codex-plugin"), { recursive: true });
    mkdirSync(join(pluginDir, "skills", "patchcord"), { recursive: true });
    mkdirSync(join(pluginDir, "skills", "patchcord-wait"), { recursive: true });
    writeFileSync(join(pluginDir, ".codex-plugin", "plugin.json"), JSON.stringify({
      name: "patchcord",
      version: pluginVersion,
      description: "Cross-machine agent messaging — connect Codex, Claude Code, claude.ai, ChatGPT, and other agents across projects and machines.",
      author: { name: "ppravdin", url: "https://patchcord.dev" },
      homepage: "https://patchcord.dev",
      repository: "https://github.com/ppravdin/patchcord",
      license: "MIT",
      keywords: ["messaging", "agents", "mcp"],
      skills: "./skills/",
      interface: {
        displayName: "Patchcord",
        shortDescription: "Cross-machine agent messaging",
        longDescription: "Connect AI agents across machines and platforms. Send messages between Codex, Claude Code, claude.ai, ChatGPT, and other MCP-connected agents.",
        developerName: "ppravdin",
        category: "Productivity",
        capabilities: ["Read", "Write"],
        websiteURL: "https://patchcord.dev",
        defaultPrompt: ["Check patchcord inbox for messages from other agents"],
      },
    }, null, 2) + "\n");
    writeFileSync(join(pluginDir, "skills", "patchcord", "SKILL.md"),
      readFileSync(join(pluginRoot, "per-project-skills", "codex", "SKILL.md"), "utf-8"));
    writeFileSync(join(pluginDir, "skills", "patchcord-wait", "SKILL.md"),
      readFileSync(join(pluginRoot, "skills", "patchcord-wait", "SKILL.md"), "utf-8"));

    // Personal marketplace entry (relative path from marketplace root)
    const marketplacePath = join(marketplaceDir, "marketplace.json");
    let marketplace = { name: "patchcord", interface: { displayName: "Patchcord" }, plugins: [] };
    if (existsSync(marketplacePath)) {
      try { marketplace = JSON.parse(readFileSync(marketplacePath, "utf-8")); } catch {}
    }
    if (!marketplace.plugins) marketplace.plugins = [];
    marketplace.plugins = marketplace.plugins.filter(p => p.name !== "patchcord");
    marketplace.plugins.push({
      name: "patchcord",
      source: { source: "local", path: "./patchcord" },
      policy: { installation: "INSTALLED_BY_DEFAULT", authentication: "ON_INSTALL" },
      category: "Productivity",
    });
    writeFileSync(marketplacePath, JSON.stringify(marketplace, null, 2) + "\n");

    // Also install global skills (working @patchcord — plugin/read broken in Codex v0.117)
    const globalSkillDir = join(homedir(), ".agents", "skills", "patchcord");
    mkdirSync(globalSkillDir, { recursive: true });
    writeFileSync(join(globalSkillDir, "SKILL.md"),
      readFileSync(join(pluginRoot, "per-project-skills", "codex", "SKILL.md"), "utf-8"));
    const globalWaitDir = join(homedir(), ".agents", "skills", "patchcord-wait");
    mkdirSync(globalWaitDir, { recursive: true });
    writeFileSync(join(globalWaitDir, "SKILL.md"),
      readFileSync(join(pluginRoot, "skills", "patchcord-wait", "SKILL.md"), "utf-8"));

    console.log(`\n  ${green}✓${r} Codex configured: ${dim}${configPath}${r}`);
    console.log(`  ${green}✓${r} Plugin installed: ${dim}@patchcord${r}, ${dim}@patchcord-wait${r}`);
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
            "X-Patchcord-Machine": hostname,
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

    // Enable patchcord statusline (agent identity + inbox count in status bar)
    try {
      const enableScript = join(pluginRoot, "scripts", "enable-statusline.sh");
      if (existsSync(enableScript)) {
        execSync(`bash "${enableScript}" --full`, { stdio: "ignore" });
        console.log(`  ${green}✓${r} Statusline enabled`);
      }
    } catch {}
  }

  // Warn about gitignore for per-project configs with tokens
  if (!isWindsurf && !isGemini && !isZed && !isOpenClaw && !isAntigravity && !isCline) {
    const gitignorePath = join(cwd, ".gitignore");
    const configFile = isCodex ? ".codex/config.toml" : isCursor ? ".cursor/mcp.json" : isVSCode ? ".vscode/mcp.json" : isOpenCode ? "opencode.json" : ".mcp.json";
    let needsWarning = true;
    if (existsSync(gitignorePath)) {
      const gi = readFileSync(gitignorePath, "utf-8");
      // Only check patterns relevant to this agent's config file
      const patterns = [configFile];
      if (isCodex) patterns.push(".codex/");
      else if (isCursor) patterns.push(".cursor/");
      else if (isVSCode) patterns.push(".vscode/");
      if (patterns.some(p => gi.includes(p))) needsWarning = false;
    }
    if (needsWarning) {
      console.log(`\n  ${yellow}⚠ Add ${configFile} to .gitignore — it contains your token${r}`);
    }
  }

  const toolName = isAntigravity ? "Antigravity" : isCline ? "Cline" : isOpenClaw ? "OpenClaw" : isOpenCode ? "OpenCode" : isZed ? "Zed" : isVSCode ? "VS Code" : isGemini ? "Gemini CLI" : isWindsurf ? "Windsurf" : isCursor ? "Cursor" : isCodex ? "Codex" : "Claude Code";

  if (!isWindsurf && !isGemini && !isZed && !isOpenClaw && !isAntigravity && !isCline) {
    console.log(`\n  ${dim}To connect a second agent:${r}`);
    console.log(`  ${dim}cd into another project and run${r} ${bold}npx patchcord@latest${r} ${dim}there.${r}`);
  }

  if (isOpenClaw) {
    console.log(`\n${dim}Run${r} ${bold}openclaw gateway restart${r}${dim}, then tools will be available in your channels.${r}`);
  } else {
    console.log(`\n  ${green}→${r} ${bold}Restart your ${toolName} session${r}, then say: ${cyan}${bold}check inbox${r}`);
  }
  process.exit(0);
}

// ── channel: spawn the channel MCP server (used by .mcp.json) ──
if (cmd === "channel") {
  const channelScript = join(pluginRoot, "channel", "server.ts");
  if (!existsSync(channelScript)) {
    console.error("Channel server not found. Reinstall patchcord.");
    process.exit(1);
  }
  // Prefer bun, fall back to node (tsx)
  const hasBun = run("which bun");
  if (hasBun) {
    const { spawnSync } = await import("child_process");
    // Install deps if needed
    const channelDir = join(pluginRoot, "channel");
    if (!existsSync(join(channelDir, "node_modules"))) {
      spawnSync("bun", ["install", "--no-summary"], { cwd: channelDir, stdio: "inherit" });
    }
    const result = spawnSync("bun", ["run", channelScript], { stdio: "inherit", env: process.env });
    process.exit(result.status ?? 1);
  } else {
    console.error("Channel plugin requires bun. Install from https://bun.sh");
    process.exit(1);
  }
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

  if (!baseUrl || !token || !isSafeToken(token)) {
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

  if (!namespace || !agentId || !isSafeId(namespace) || !isSafeId(agentId)) {
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
