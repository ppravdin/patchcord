#!/bin/bash
# Enable the patchcord statusline in Claude Code settings.
# Usage: bash enable-statusline.sh [--full]
#   --full: also show model, context%, repo (branch)
#
# Writes to user-level ~/.claude/settings.json unless another
# tool's statusline is already set there — in that case, writes
# to project-level .claude/settings.json to avoid overwriting.
set -euo pipefail

EXTRA_ARGS=""
for arg in "$@"; do
    [ "$arg" = "--full" ] && EXTRA_ARGS=" --full"
done

# Find the statusline script path
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATUSLINE="$SCRIPT_DIR/statusline.sh"

if [ ! -f "$STATUSLINE" ]; then
    echo "Error: statusline.sh not found at $STATUSLINE" >&2
    exit 1
fi

NEW_CMD="bash \"$STATUSLINE\"${EXTRA_ARGS}"

# Decide where to write
USER_SETTINGS="$HOME/.claude/settings.json"
mkdir -p "$HOME/.claude"

SETTINGS="$USER_SETTINGS"

if [ -f "$USER_SETTINGS" ]; then
    EXISTING_CMD=$(jq -r '.statusLine.command // ""' "$USER_SETTINGS" 2>/dev/null || true)
    if [ -n "$EXISTING_CMD" ]; then
        # Already has a statusline — is it ours?
        if echo "$EXISTING_CMD" | grep -q "patchcord"; then
            # Ours — update in place (e.g. add/remove --full)
            SETTINGS="$USER_SETTINGS"
        else
            # Another tool's statusline — don't overwrite, use project
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
                SETTINGS="$PROJECT_ROOT/.claude/settings.json"
                mkdir -p "$PROJECT_ROOT/.claude"
            else
                # No project found — skip, don't overwrite another tool
                echo "Statusline: another tool's statusline is set globally. Skipping."
                exit 0
            fi
        fi
    fi
fi

# Read existing settings or start fresh
if [ -f "$SETTINGS" ]; then
    CURRENT=$(cat "$SETTINGS")
else
    CURRENT='{}'
fi

# Set statusLine field
UPDATED=$(echo "$CURRENT" | jq --arg cmd "$NEW_CMD" '.statusLine = {"type": "command", "command": $cmd}')
echo "$UPDATED" > "$SETTINGS"
