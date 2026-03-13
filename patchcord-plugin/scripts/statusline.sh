#!/bin/bash
# Patchcord statusline for Claude Code.
#
# Default: shows only patchcord identity + inbox count.
# With --full: also shows model, context%, repo (branch).
#
# --full mode model/context/git display based on claude-statusline by Kamran Ahmed
# https://github.com/kamranahmedse/claude-statusline (MIT)
#
# Receives session JSON on stdin, outputs ANSI-formatted text.
set -f

FULL=false
for arg in "$@"; do
    [ "$arg" = "--full" ] && FULL=true
done

find_patchcord_mcp_json() {
    local dir="$1"
    while [ -n "$dir" ] && [ "$dir" != "/" ]; do
        if [ -f "$dir/.mcp.json" ]; then
            printf '%s\n' "$dir/.mcp.json"
            return 0
        fi
        dir=$(dirname "$dir")
    done
    return 1
}

input=$(cat)

if [ -z "$input" ]; then
    exit 0
fi

# ── Colors ──────────────────────────────────────────────
blue='\033[38;2;0;153;255m'
green='\033[38;2;0;175;80m'
cyan='\033[38;2;86;182;194m'
red='\033[38;2;255;85;85m'
yellow='\033[38;2;230;200;0m'
orange='\033[38;2;255;176;85m'
white='\033[38;2;220;220;220m'
dim='\033[2m'
reset='\033[0m'

sep=" ${dim}│${reset} "

# ── Patchcord: agent identity + inbox ───────────────────
cwd=$(echo "$input" | jq -r '.cwd // ""')
[ -z "$cwd" ] || [ "$cwd" = "null" ] && cwd=$(pwd)

pc_token=""
pc_url=""
mcp_json=$(find_patchcord_mcp_json "$cwd" || true)

if [ -n "$mcp_json" ]; then
    mcp_url=$(jq -r '.mcpServers.patchcord.url // empty' "$mcp_json" 2>/dev/null || true)
    mcp_auth=$(jq -r '.mcpServers.patchcord.headers.Authorization // empty' "$mcp_json" 2>/dev/null || true)
    if [ -n "$mcp_url" ] && [ -n "$mcp_auth" ]; then
        pc_url="${mcp_url%/mcp}"
        pc_token="${mcp_auth#Bearer }"
    fi
fi

pc_part=""
if [ -n "$pc_url" ] && [ -n "$pc_token" ]; then
    cache_key=$(printf '%s\n%s\n' "$mcp_json" "$pc_url" | sha256sum | awk '{print $1}')
    cache_file="/tmp/claude/patchcord-statusline-${cache_key}.json"
    cache_max_age=20
    mkdir -p /tmp/claude

    needs_refresh=true
    pc_data=""

    if [ -f "$cache_file" ]; then
        cache_mtime=$(stat -c %Y "$cache_file" 2>/dev/null || stat -f %m "$cache_file" 2>/dev/null)
        now=$(date +%s)
        if [ $(( now - cache_mtime )) -lt $cache_max_age ]; then
            needs_refresh=false
            pc_data=$(cat "$cache_file" 2>/dev/null)
        fi
    fi

    if $needs_refresh; then
        http_code=$(curl -s -o /tmp/claude/patchcord-sl-resp.json -w "%{http_code}" --max-time 3 \
            -H "Authorization: Bearer $pc_token" \
            "${pc_url}/api/inbox?status=pending&limit=50" 2>/dev/null || echo "000")
        if [ "$http_code" = "401" ] || [ "$http_code" = "403" ]; then
            pc_data='{"_auth_error":true}'
            echo "$pc_data" > "$cache_file"
        elif [ "$http_code" = "200" ]; then
            pc_data=$(cat /tmp/claude/patchcord-sl-resp.json 2>/dev/null)
            [ -n "$pc_data" ] && echo "$pc_data" > "$cache_file"
        fi
        rm -f /tmp/claude/patchcord-sl-resp.json
    fi

    if [ -n "$pc_data" ]; then
        auth_error=$(echo "$pc_data" | jq -r '._auth_error // false' 2>/dev/null)
        if [ "$auth_error" = "true" ]; then
            pc_part="${red}BAD TOKEN${reset}"
        else
            agent_id=$(echo "$pc_data" | jq -r '.agent_id // empty' 2>/dev/null)
            namespace_id=$(echo "$pc_data" | jq -r '.namespace_id // empty' 2>/dev/null)
            machine=$(echo "$pc_data" | jq -r '.machine_name // empty' 2>/dev/null)
            if [ -z "$machine" ] || [ "$machine" = "null" ]; then
                machine=$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo "")
            fi
            count=$(echo "$pc_data" | jq -r '.count // .pending_count // 0' 2>/dev/null)

            if [ -n "$agent_id" ]; then
                pc_part="${white}${agent_id}${reset}"
                if [ -n "$namespace_id" ] && [ "$namespace_id" != "null" ]; then
                    pc_part+="${dim}@${namespace_id}${reset}"
                fi
                if [ -n "$machine" ]; then
                    pc_part+=" ${dim}(${machine})${reset}"
                fi
            fi

            if [ "$count" -gt 0 ] 2>/dev/null; then
                pc_part+=" ${red}${count} msg${reset}"
            fi
        fi
    fi
fi

# ── Update check (once per 24h) ───────────────────────
update_part=""
plugin_json="${CLAUDE_PLUGIN_ROOT:-.}/.claude-plugin/plugin.json"
if [ -f "$plugin_json" ]; then
    installed_ver=$(jq -r '.version // ""' "$plugin_json" 2>/dev/null)
    if [ -n "$installed_ver" ]; then
        update_cache="/tmp/claude/patchcord-update-check.json"
        mkdir -p /tmp/claude
        update_stale=true
        if [ -f "$update_cache" ]; then
            uc_mtime=$(stat -c %Y "$update_cache" 2>/dev/null || stat -f %m "$update_cache" 2>/dev/null)
            uc_now=$(date +%s)
            [ $(( uc_now - uc_mtime )) -lt 86400 ] && update_stale=false
        fi
        if $update_stale; then
            latest=$(npm view patchcord version --json 2>/dev/null | tr -d '"' || true)
            if [ -n "$latest" ]; then
                echo "{\"latest\":\"$latest\"}" > "$update_cache"
            fi
        else
            latest=$(jq -r '.latest // ""' "$update_cache" 2>/dev/null)
        fi
        if [ -n "$latest" ] && [ "$latest" != "$installed_ver" ]; then
            update_part="${yellow}⬆ ${latest} (npm update -g patchcord)${reset}"
        fi
    fi
fi

# No patchcord config — output nothing in default mode
if [ -z "$pc_part" ] && [ -z "$update_part" ] && ! $FULL; then
    exit 0
fi

# ── Build line ──────────────────────────────────────────
line=""

if $FULL; then
    model_name=$(echo "$input" | jq -r '.model.display_name // "Claude"')

    size=$(echo "$input" | jq -r '.context_window.context_window_size // 200000')
    [ "$size" -eq 0 ] 2>/dev/null && size=200000
    input_tokens=$(echo "$input" | jq -r '.context_window.current_usage.input_tokens // 0')
    cache_create=$(echo "$input" | jq -r '.context_window.current_usage.cache_creation_input_tokens // 0')
    cache_read=$(echo "$input" | jq -r '.context_window.current_usage.cache_read_input_tokens // 0')
    current=$(( input_tokens + cache_create + cache_read ))
    if [ "$size" -gt 0 ]; then
        pct_used=$(( current * 100 / size ))
    else
        pct_used=0
    fi
    if [ "$pct_used" -ge 90 ]; then pct_color="$red"
    elif [ "$pct_used" -ge 70 ]; then pct_color="$yellow"
    elif [ "$pct_used" -ge 50 ]; then pct_color="$orange"
    else pct_color="$green"
    fi

    dirname=$(basename "$cwd")
    git_branch=""
    git_dirty=""
    if git -C "$cwd" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git_branch=$(git -C "$cwd" symbolic-ref --short HEAD 2>/dev/null)
        if [ -n "$(git -C "$cwd" status --porcelain 2>/dev/null)" ]; then
            git_dirty="*"
        fi
    fi

    line="${blue}${model_name}${reset}"
    line+="${sep}"
    line+="${pct_color}${pct_used}%${reset}"
    line+="${sep}"
    line+="${cyan}${dirname}${reset}"
    if [ -n "$git_branch" ]; then
        line+=" ${green}(${git_branch}${red}${git_dirty}${green})${reset}"
    fi
    if [ -n "$pc_part" ]; then
        line+="${sep}"
        line+="${pc_part}"
    fi
    if [ -n "$update_part" ]; then
        line+="${sep}"
        line+="${update_part}"
    fi
else
    line="${pc_part}"
    if [ -n "$update_part" ]; then
        if [ -n "$line" ]; then
            line+="${sep}"
        fi
        line+="${update_part}"
    fi
fi

# ── Output ──────────────────────────────────────────────
printf "%b" "$line"
exit 0
