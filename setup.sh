#!/usr/bin/env bash
# One-command patchcord direct-mode setup.
#
# Usage:
#   bash setup.sh <namespace_id> <agent_id> <supabase_url> <supabase_key> [db_password]
#
# The first 4 args are required. If db_password is provided, the script
# also runs the Supabase migration (creates tables automatically).
#
# This script prepares the direct-mode runtime in this patchcord directory.
#
# Example:
#   bash setup.sh default thick https://xxx.supabase.co eyJhbG... my-db-password

set -euo pipefail

NAMESPACE_ID="${1:-}"
AGENT_ID="${2:-}"
SUPABASE_URL="${3:-}"
SUPABASE_KEY="${4:-}"
DB_PASSWORD="${5:-}"

if [ -z "$NAMESPACE_ID" ] || [ -z "$AGENT_ID" ] || [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_KEY" ]; then
  echo "Usage: bash setup.sh <namespace_id> <agent_id> <supabase_url> <supabase_key> [db_password]"
  echo ""
  echo "  namespace_id   - namespace for this patchcord install (e.g. 'default')"
  echo "  agent_id       - default agent_id for this patchcord install"
  echo "  supabase_url   - project URL from Supabase Settings > API"
  echo "  supabase_key   - service_role key from Supabase Settings > API (recommended)"
  echo "  db_password    - (optional) DB password from Supabase Settings > Database"
  echo "                   If provided, creates tables automatically."
  echo "                   If omitted, you must run migrations/*.sql manually."
  exit 1
fi

DIR="$(cd "$(dirname "$0")" && pwd)"

# Find Python 3.10+
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if ! command -v "$candidate" >/dev/null 2>&1; then
    continue
  fi
  if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "ERROR: Python 3.10+ is required. Found none."
  echo "Install python3.12 and try again."
  exit 1
fi

echo "Using $PYTHON ($($PYTHON --version))"

# Create venv and install deps
echo "Creating venv..."
$PYTHON -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --upgrade pip -q
"$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt" -q
echo "Deps installed."

# Run migration if DB password provided
if [ -n "$DB_PASSWORD" ]; then
  echo ""
  echo "Running Supabase migration..."
  "$DIR/.venv/bin/python" -m patchcord.cli.migrate "$SUPABASE_URL" "$DB_PASSWORD"
else
  echo ""
  echo "NOTE: No db_password provided. Run migrations/*.sql in Supabase SQL Editor,"
  echo "      or re-run with: bash setup.sh $NAMESPACE_ID $AGENT_ID $SUPABASE_URL $SUPABASE_KEY <db_password>"
fi

# Write .env
cat > "$DIR/.env" <<EOF
NAMESPACE_ID="$NAMESPACE_ID"
AGENT_ID="$AGENT_ID"
SUPABASE_URL="$SUPABASE_URL"
SUPABASE_KEY="$SUPABASE_KEY"
EOF
echo ""
echo ".env written (NAMESPACE_ID=$NAMESPACE_ID, AGENT_ID=$AGENT_ID)."

# Verify the direct-mode server can import and start
echo ""
echo "Verifying import..."
env \
  NAMESPACE_ID="$NAMESPACE_ID" \
  AGENT_ID="$AGENT_ID" \
  SUPABASE_URL="$SUPABASE_URL" \
  SUPABASE_KEY="$SUPABASE_KEY" \
  "$DIR/.venv/bin/python" -c "from patchcord.direct.server import mcp; print('OK - patchcord direct mode imports clean.')"

echo ""
echo "Direct-mode runtime is ready."
echo ""
echo "To wire a Claude Code project, register the MCP server in that project:"
echo "  claude mcp add patchcord -s project \\"
echo "    -e NAMESPACE_ID=$NAMESPACE_ID \\"
echo "    -e AGENT_ID=$AGENT_ID \\"
echo "    -e SUPABASE_URL=$SUPABASE_URL \\"
echo "    -e SUPABASE_KEY=$SUPABASE_KEY \\"
echo "    -- \"$DIR/.venv/bin/python\" -m patchcord.direct.server"
echo ""
echo "See docs/client-setup.md for full details."
echo "Done."
