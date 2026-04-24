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
     command: "exec node \"<absolute-path-to-subscribe.mjs>\""
   )
   ```
   No `2>&1`, no grep filter. By construction the script only writes
   `PATCHCORD: ...` lines to stdout. Everything else — `connected`,
   `token refreshed`, startup diagnostics, errors — goes to stderr,
   which Monitor captures into its output file but does NOT fire as
   notifications. So there's nothing to filter and no way to get the
   filter wrong.

   Crash detection is handled automatically by Monitor itself: when the
   process exits, Monitor emits a built-in "stream ended" task
   notification with the output file path and exit code.

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

# If the Monitor stream ends — STRICT PROTOCOL

The stream-end task notification includes the path to Monitor's output
file. Do exactly this, in this order:

1. Read that output file using the Read tool.
2. Look at the last ~15 lines for a line matching one of the known
   failure strings below. There will always be at least one terminal
   error line — the script's error handlers guarantee it.
3. Report the specific cause to the user in one short sentence.
4. STOP.

**Forbidden on failure — do not do any of these:**
- Do NOT run `pgrep`, `ps`, `kill`, `pkill`, `killall`, or any command
  that targets PIDs or process names.
- Do NOT modify, delete, or write to the pidfile yourself. The script
  manages it; it's already cleaned up by the time Monitor emits the
  stream-end event.
- Do NOT spawn another Monitor or another `node subscribe.mjs`. One
  failure means something is wrong with the config or environment;
  respawning will not fix it and will make things worse.
- Do NOT search for orphaned processes or try to "clean up" state.

Concrete failure strings you may see and what they mean:

- `no .mcp.json in <cwd>` — session is not in a patchcord project dir.
  Tell the user which directory to `cd` into.
- `ticket: token rejected (HTTP 401|403)` — bearer in `.mcp.json` is
  bad; regenerate from the dashboard.
- `ticket: server not configured for realtime` — the patchcord server
  hasn't had `SUPABASE_JWT_SECRET` / `SUPABASE_ANON_KEY` set. This is
  a cloud-only feature.
- `ticket: namespace not owned — regenerate your token` — the token's
  namespace lost its owner row; regenerate from the dashboard.
- `already running (pid N)` (exit code 2) — pidfile guard tripped,
  another listener is active. Report and stop. Do NOT kill the other
  listener to make room.
- `subscribe: fatal: ...` — unhandled error. Show the user the line
  verbatim, stop.

If the process exited cleanly (exit 0) with no error line, the user
closed the session or killed the process. Nothing to do.
