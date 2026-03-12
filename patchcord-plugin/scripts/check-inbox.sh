#!/bin/bash
set -euo pipefail

find_patchcord_mcp_json() {
  local dir="${1:-$PWD}"
  while [ -n "$dir" ] && [ "$dir" != "/" ]; do
    if [ -f "$dir/.mcp.json" ]; then
      printf '%s\n' "$dir/.mcp.json"
      return 0
    fi
    dir=$(dirname "$dir")
  done
  return 1
}

INPUT=$(cat)

# Guard against infinite loops: stop_hook_active is true when Claude
# is already continuing because a previous Stop hook told it to.
STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
if [ "$STOP_ACTIVE" = "true" ]; then
  exit 0
fi

# Resolve config from project-scoped .mcp.json only.
TOKEN=""
URL=""
MCP_JSON=$(find_patchcord_mcp_json "$PWD" || true)

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
HTTP_CODE=$(curl -s -o /tmp/patchcord_inbox.json -w "%{http_code}" --max-time 5 \
  -H "Authorization: Bearer ${TOKEN}" \
  "${URL}/api/inbox?status=pending&limit=1" 2>/dev/null || echo "000")

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

COUNT=$(echo "$RESPONSE" | jq -r '.count // .pending_count // 0')

if [ "$COUNT" -gt 0 ]; then
  jq -n --arg count "$COUNT" '{
    "decision": "block",
    "reason": ($count + " patchcord message(s) waiting. Call inbox() and reply to all immediately.")
  }'
else
  exit 0
fi
