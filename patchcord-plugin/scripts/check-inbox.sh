#!/bin/bash
set -euo pipefail

# jq is required — skip silently if not installed (fresh macOS)
command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat)

# Get project cwd from Claude Code's input JSON, fall back to $PWD
PROJECT_CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)
[ -z "$PROJECT_CWD" ] || [ "$PROJECT_CWD" = "null" ] && PROJECT_CWD="$PWD"

# Guard against infinite loops: stop_hook_active is true when Claude
# is already continuing because a previous Stop hook told it to.
STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null || echo "false")
if [ "$STOP_ACTIVE" = "true" ]; then
  exit 0
fi

# ── Update check (once per session, first run only) ───────────
UPDATE_FLAG="/tmp/patchcord_update_checked_$$"
if [ ! -f "$UPDATE_FLAG" ]; then
  touch "$UPDATE_FLAG"
  plugin_json="${CLAUDE_PLUGIN_ROOT:-.}/.claude-plugin/plugin.json"
  if [ -f "$plugin_json" ]; then
    installed_ver=$(jq -r '.version // ""' "$plugin_json" 2>/dev/null)
    if [ -n "$installed_ver" ]; then
      latest=$(npm view patchcord version --json 2>/dev/null | tr -d '"' || true)
      if [ -n "$latest" ] && [ "$latest" != "$installed_ver" ]; then
        echo "⬆ Patchcord plugin update: v${installed_ver} → v${latest}. Run: npx patchcord@latest install" >&2
      fi
    fi
  fi
fi

# Resolve config from project-scoped .mcp.json only.
TOKEN=""
URL=""
MCP_JSON=""
[ -f "$PROJECT_CWD/.mcp.json" ] && MCP_JSON="$PROJECT_CWD/.mcp.json"

if [ -n "$MCP_JSON" ]; then
  MCP_URL=$(jq -r '.mcpServers.patchcord.url // empty' "$MCP_JSON" 2>/dev/null || true)
  MCP_AUTH=$(jq -r '.mcpServers.patchcord.headers.Authorization // empty' "$MCP_JSON" 2>/dev/null || true)
  if [ -n "$MCP_URL" ] && [ -n "$MCP_AUTH" ]; then
    URL="${MCP_URL%/mcp}"
    URL="${URL%/mcp/bearer}"
    TOKEN="${MCP_AUTH#Bearer }"
  fi
fi

if [ -z "$URL" ] || [ -z "$TOKEN" ]; then
  exit 0  # Not configured, skip silently
fi

# Check inbox — one lightweight HTTP call
MACHINE_NAME=$(hostname -s 2>/dev/null || echo "unknown")
INSTALL_PATH=$(dirname "$MCP_JSON")
HTTP_CODE=$(curl -s -o /tmp/patchcord_inbox.json -w "%{http_code}" --max-time 5 \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "x-patchcord-machine: ${MACHINE_NAME}" \
  -H "x-patchcord-install-path: ${INSTALL_PATH}" \
  "${URL}/api/inbox?status=pending&limit=5&count_only=1" 2>/dev/null || echo "000")

if [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "403" ]; then
  jq -n '{
    "decision": "block",
    "reason": "PATCHCORD AUTH FAILED: token rejected by server (HTTP '"$HTTP_CODE"'). Check your token in .mcp.json — it may be wrong, expired, or not yet registered on the server."
  }'
  rm -f /tmp/patchcord_inbox.json
  exit 0
fi

if [ "$HTTP_CODE" = "000" ]; then
  # Server unreachable — skip silently
  rm -f /tmp/patchcord_inbox.json
  exit 0
fi

RESPONSE=$(cat /tmp/patchcord_inbox.json 2>/dev/null || echo '{"count":0}')
rm -f /tmp/patchcord_inbox.json

# ── Auto-apply custom skill from web console ──────────────────
# Writes to .claude/skills/patchcord-custom/SKILL.md — Claude Code
# native project-level skill directory. Auto-discovered by Claude.
NAMESPACE=$(echo "$RESPONSE" | jq -r '.namespace_id // empty' 2>/dev/null || true)
AGENT_ID=$(echo "$RESPONSE" | jq -r '.agent_id // empty' 2>/dev/null || true)

if [ -n "$NAMESPACE" ] && [ -n "$AGENT_ID" ]; then
  SKILL_RESP=$(curl -s --max-time 3 \
    -H "Authorization: Bearer ${TOKEN}" \
    "${URL}/api/skills/${NAMESPACE}/${AGENT_ID}" 2>/dev/null || true)

  if [ -n "$SKILL_RESP" ]; then
    SKILL_TEXT=$(echo "$SKILL_RESP" | jq -r '.skill_text // empty' 2>/dev/null || true)
    SKILL_HASH=$(printf '%s' "$SKILL_TEXT" | (md5sum 2>/dev/null || md5 2>/dev/null) | cut -d' ' -f1 || echo "nohash")
    CACHE_FILE="/tmp/patchcord_skill_hash_${NAMESPACE}_${AGENT_ID}"
    OLD_HASH=$(cat "$CACHE_FILE" 2>/dev/null || echo "")

    if [ -n "$SKILL_TEXT" ] && [ "$SKILL_HASH" != "$OLD_HASH" ]; then
      PROJECT_ROOT=$(dirname "$MCP_JSON")
      SKILL_DIR="${PROJECT_ROOT}/.claude/skills/patchcord-custom"
      SKILL_FILE="${SKILL_DIR}/SKILL.md"
      mkdir -p "$SKILL_DIR"
      printf '%s\n' "$SKILL_TEXT" > "$SKILL_FILE"
      echo "$SKILL_HASH" > "$CACHE_FILE"
      # Clean up old PATCHCORD.md if it exists
      rm -f "${PROJECT_ROOT}/PATCHCORD.md"
    fi
  fi
fi

# ── Inbox notification (deduplicated across Stop + Notification hooks) ──
COUNT=$(echo "$RESPONSE" | jq -r '.count // .pending_count // 0' 2>/dev/null || echo "0")

if [ "$COUNT" -gt 0 ]; then
  NOTIFY_LOCK="/tmp/patchcord_notify_lock"
  LOCK_AGE=5
  if [ -f "$NOTIFY_LOCK" ]; then
    LOCK_MTIME=$(stat -c %Y "$NOTIFY_LOCK" 2>/dev/null || stat -f %m "$NOTIFY_LOCK" 2>/dev/null || echo "0")
    NOW=$(date +%s)
    if [ $(( NOW - LOCK_MTIME )) -lt $LOCK_AGE ]; then
      exit 0  # Already notified within 5s
    fi
  fi
  touch "$NOTIFY_LOCK"
  jq -n --arg count "$COUNT" '{
    "decision": "block",
    "reason": ($count + " patchcord message(s) waiting. Call inbox() and reply to all immediately.")
  }'
else
  exit 0
fi
