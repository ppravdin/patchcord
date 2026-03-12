#!/bin/bash
# Enable the patchcord statusline in Claude Code user settings.
# Usage: bash enable-statusline.sh [--full]
#   --full: also show model, context%, repo (branch)
set -euo pipefail

EXTRA_ARGS=""
for arg in "$@"; do
    [ "$arg" = "--full" ] && EXTRA_ARGS=" --full"
done

SETTINGS="$HOME/.claude/settings.json"
mkdir -p "$HOME/.claude"

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
