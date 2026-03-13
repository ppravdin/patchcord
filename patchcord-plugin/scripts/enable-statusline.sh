#!/bin/bash
# Enable the patchcord statusline in Claude Code user settings.
# Usage: bash enable-statusline.sh [--full]
#   --full: also show model, context%, repo (branch)
set -euo pipefail

EXTRA_ARGS=""
for arg in "$@"; do
    [ "$arg" = "--full" ] && EXTRA_ARGS=" --full"
done

# Find the project root (walk up from cwd to find .mcp.json with patchcord)
find_project_root() {
    local dir="${1:-$(pwd)}"
    while [ -n "$dir" ] && [ "$dir" != "/" ]; do
        if [ -f "$dir/.mcp.json" ] && jq -e '.mcpServers.patchcord' "$dir/.mcp.json" >/dev/null 2>&1; then
            printf '%s\n' "$dir"
            return 0
        fi
        dir=$(dirname "$dir")
    done
    return 1
}

PROJECT_ROOT=$(find_project_root || true)

if [ -n "$PROJECT_ROOT" ]; then
    # Project-level settings (only affects this repo)
    SETTINGS="$PROJECT_ROOT/.claude/settings.json"
    mkdir -p "$PROJECT_ROOT/.claude"
else
    # Fallback to user-level if no project found
    SETTINGS="$HOME/.claude/settings.json"
    mkdir -p "$HOME/.claude"
fi

# Find the plugin's statusline script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATUSLINE="$SCRIPT_DIR/statusline.sh"

if [ ! -f "$STATUSLINE" ]; then
    echo "Error: statusline.sh not found at $STATUSLINE" >&2
    exit 1
fi

# Read existing settings or start fresh
if [ -f "$SETTINGS" ]; then
    CURRENT=$(cat "$SETTINGS")
else
    CURRENT='{}'
fi

# Set statusLine field
UPDATED=$(echo "$CURRENT" | jq --arg cmd "bash \"$STATUSLINE\"${EXTRA_ARGS}" '.statusLine = {"type": "command", "command": $cmd}')
echo "$UPDATED" > "$SETTINGS"

echo "Patchcord statusline enabled."
echo "  Script: $STATUSLINE"
echo "  Settings: $SETTINGS"
echo "Restart Claude Code to see the statusline."
