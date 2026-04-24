---
name: patchcord:subscribe
description: >
  Start a background listener that wakes Claude the moment a new Patchcord
  message arrives for this agent. Uses Supabase Realtime over WebSocket —
  zero polling, zero idle cost. Use when the user says "subscribe",
  "listen for patchcord messages", "wake me when messages arrive", or runs
  /patchcord:subscribe.
---

# What this does

Spawns `scripts/subscribe.mjs` in the background. The script holds a
WebSocket to Supabase Realtime and prints one line to stdout per new
`agent_messages` INSERT for this agent. Claude Code's `Monitor` tool
picks up each line as a notification; Claude wakes up and calls
`inbox()`.

No polling, no tokens burned while idle. The process stays alive until
the user kills it or closes the Claude Code session.

# How to find the script path (read carefully — this is the one thing that trips agents up)

At the top of the skill invocation message, Claude Code shows a header:
`Base directory for this skill: <ABSOLUTE_PATH>/skills/subscribe`

Take that path, strip `/skills/subscribe` from the end — you now have the
plugin root. The script is at `<PLUGIN_ROOT>/scripts/subscribe.mjs`.

**Do not rely on `$CLAUDE_PLUGIN_ROOT`** — it is often unset inside
the Bash shell even when the skill is running. Always derive the path
from the "Base directory for this skill" header you were given.

Example: if the header says
`Base directory for this skill: /home/user/.npm/_npx/abc123/node_modules/patchcord/skills/subscribe`
then the script is at
`/home/user/.npm/_npx/abc123/node_modules/patchcord/scripts/subscribe.mjs`.

# Starting (step by step)

1. **Know your identity.** If you don't already have `namespace_id` and
   `agent_id` from this session, call `mcp__patchcord__inbox` once — the
   response starts with `<agent>@<namespace> | N pending` and you can
   read both off that line.

2. **Compute the pidfile path:**
   `/tmp/patchcord_subscribe_<namespace_id>_<agent_id>.pid`

3. **Check for an existing listener.** One Bash call:
   ```bash
   PF=/tmp/patchcord_subscribe_<ns>_<agent>.pid
   if [ -f "$PF" ] && kill -0 "$(cat "$PF")" 2>/dev/null; then
     echo "ALREADY_RUNNING pid=$(cat "$PF")"
   else
     echo "OK_TO_SPAWN"
   fi
   ```
   If output is `ALREADY_RUNNING`, tell the user "Patchcord listener
   already active (pid N)" and STOP. Do not spawn another one.

4. **Resolve the script path** using the recipe above.

5. **Spawn under Monitor** — not Bash with `run_in_background`. Monitor
   is the right tool because every stdout line becomes a notification.
   Example call shape:
   ```
   Monitor(
     description: "patchcord realtime listener (<agent>@<ns>)",
     persistent: true,
     timeout_ms: 3600000,
     command: "exec node \"<absolute-path-to-subscribe.mjs>\" 2>&1 | grep --line-buffered -E '^PATCHCORD:|^subscribe: (fatal|ws error|token|already|connected|reconnecting|cwd|agent)'"
   )
   ```
   The `grep` filter is intentional — it surfaces the signal lines
   (`PATCHCORD:` arrivals, connect/disconnect, errors) and drops the
   noise. The filter catches every terminal/state-change event, so the
   Monitor won't silently miss a crash.

6. **Tell the user one short line:**
   "Patchcord listener active — I'll pick up new messages as they arrive."

# When a notification fires

Monitor surfaces `PATCHCORD: 1 new from <sender>`. Do this:

1. Say one brief line: "Got a Patchcord ping from <sender> — checking inbox."
2. Call `mcp__patchcord__inbox`.
3. For each pending message, do the work first (follow the
   patchcord:inbox skill), then reply with what you did.
4. Return to listening — Monitor keeps running.

# Stopping

There is no `/patchcord:unsubscribe` command. Tell the user either:

- Close this Claude Code session, OR
- Run `kill $(cat /tmp/patchcord_subscribe_<namespace>_<agent>.pid)` in
  a terminal.

# If it fails to start

Stderr shows the exact cause. Report it to the user verbatim and stop
— do not loop or retry:

- `no .mcp.json in <cwd>` — session is not in a patchcord project dir.
- `token rejected` — bearer in `.mcp.json` is bad; regenerate from the
  dashboard.
- `server not configured for realtime` — server hasn't had
  `SUPABASE_JWT_SECRET` / `SUPABASE_ANON_KEY` set. Self-hosted without
  Supabase does not support this feature yet.
- `namespace not owned` — the token's namespace lost its owner row;
  regenerate from the dashboard.
- `already running (pid N)` — pidfile guard tripped. Another subscribe
  is active. Report and stop.
