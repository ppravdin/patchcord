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
`agent_messages` INSERT for this agent. Claude Code's Monitor tool picks
up each line as a notification; Claude wakes up and calls `inbox()`.

No polling, no tokens burned while idle. The process stays alive until
the user kills it or closes the Claude Code session.

# Starting

1. Find namespace + agent_id. Call `mcp__patchcord__inbox` if you don't
   already know them from this session. The response contains `namespace_id`
   and `agent_id`.
2. Compute pidfile: `/tmp/patchcord_subscribe_<namespace_id>_<agent_id>.pid`.
3. Check if a listener is already running:
   - If the pidfile exists AND `kill -0 $(cat <pidfile>)` succeeds →
     tell the user "Patchcord listener already active (pid N)" and stop.
     Do NOT spawn another one.
   - If the pidfile exists but the PID is dead → the subscribe script
     itself cleans up stale pidfiles on startup, so just proceed.
4. Find the script at `$CLAUDE_PLUGIN_ROOT/scripts/subscribe.mjs`.
5. Run it in the background with Bash `run_in_background: true`:
   ```
   node "$CLAUDE_PLUGIN_ROOT/scripts/subscribe.mjs"
   ```
6. Attach the `Monitor` tool to that background shell so its stdout
   becomes a stream of notifications.
7. Tell the user one short line:
   "Patchcord listener active — I'll pick up new messages as they arrive."

# When a notification fires

Monitor surfaces a line like `PATCHCORD: 1 new from backend`. Do this:

1. Say one brief line in chat so the user can see you got pinged:
   "Got a Patchcord ping from <sender> — checking inbox."
2. Call `mcp__patchcord__inbox`.
3. For each pending message: do the work first (follow the
   patchcord:inbox skill), then reply with what you did.
4. Return to listening — Monitor keeps running.

# Stopping

There is no `/patchcord:unsubscribe` command. Tell the user either:

- Close this Claude Code session (the background process will keep
  running unless they kill it — see below), OR
- Run `kill $(cat /tmp/patchcord_subscribe_<namespace>_<agent>.pid)` in
  a terminal.

# If it fails to start

The script exits 1 with a clear stderr message in these cases:

- `no .mcp.json in <cwd>` — the Claude session is not in a patchcord
  project directory.
- `token rejected` — the bearer in `.mcp.json` is bad; regenerate from
  the dashboard.
- `server not configured for realtime` — the server hasn't had
  `SUPABASE_JWT_SECRET` / `SUPABASE_ANON_KEY` set. This is a cloud-only
  feature for now. Tell the user.
- `namespace not owned` — the token's namespace lost its owner row;
  regenerate from the dashboard.
- `already running (pid N)` — another subscribe is already active.
  Report that to the user, do not try again.

In all of these, report the exact error to the user and stop — don't
loop or retry.
