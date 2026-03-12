#!/usr/bin/env python3
"""
Run migrations against your Supabase Postgres.

Usage:
  python -m patchcord.cli.migrate <supabase_url> <db_password>

Example:
  python -m patchcord.cli.migrate https://abcdef.supabase.co my-db-password

The project ref is extracted from the URL automatically.
Tries direct connection first, then session-mode poolers across regions.

NOTE: Supabase pooler uses "postgres.projectref" as the username.
libpq misparses the dot in keyword args, so we must use a DSN string
with the dot percent-encoded as %2E. This is a known Supabase/libpq issue.
"""

import os
import re
import sys
import urllib.parse

REGIONS = [
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
    "eu-central-1",
    "eu-west-1",
    "eu-west-2",
    "ap-southeast-1",
    "ap-northeast-1",
    "ap-south-1",
    "sa-east-1",
]

PREFIXES = ["aws-0", "aws-1"]


def _find_migrations_dir() -> str:
    """Locate the migrations/ directory relative to the repo root."""
    # Walk up from this file to find the repo root (where migrations/ lives)
    cli_dir = os.path.dirname(os.path.abspath(__file__))
    # patchcord/cli/ -> patchcord/ -> repo root
    repo_root = os.path.dirname(os.path.dirname(cli_dir))
    migrations_dir = os.path.join(repo_root, "migrations")
    if os.path.isdir(migrations_dir):
        return migrations_dir
    # Fallback: check next to the old root-level migrate.py
    fallback = os.path.join(repo_root, "supabase_setup.sql")
    if os.path.exists(fallback):
        return repo_root
    return migrations_dir  # return expected path even if missing (for error msg)


def _load_sql(migrations_dir: str) -> str:
    """Load and concatenate all .sql files from migrations/ in sorted order."""
    if not os.path.isdir(migrations_dir):
        print(f"ERROR: Migrations directory not found: {migrations_dir}")
        sys.exit(1)

    sql_files = sorted(f for f in os.listdir(migrations_dir) if f.endswith(".sql"))
    if not sql_files:
        print(f"ERROR: No .sql files found in {migrations_dir}")
        sys.exit(1)

    parts = []
    for sql_file in sql_files:
        path = os.path.join(migrations_dir, sql_file)
        with open(path) as f:
            parts.append(f"-- {sql_file}\n{f.read()}")
    return "\n\n".join(parts)


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python -m patchcord.cli.migrate <supabase_url> <db_password>")
        print("  supabase_url  - e.g. https://abcdef.supabase.co")
        print("  db_password   - from Supabase Settings > Database")
        sys.exit(1)

    supabase_url = sys.argv[1].rstrip("/")
    db_password = sys.argv[2]

    match = re.search(r"https://([a-z0-9]+)\.supabase\.co", supabase_url)
    if not match:
        print(f"ERROR: Cannot extract project ref from URL: {supabase_url}")
        print("Expected format: https://<project-ref>.supabase.co")
        sys.exit(1)

    project_ref = match.group(1)
    escaped_password = urllib.parse.quote(db_password, safe="")

    migrations_dir = _find_migrations_dir()
    sql = _load_sql(migrations_dir)

    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    def try_connect_and_run(dsn, label):
        """Try to connect via DSN and run migration. Returns (ok, error)."""
        try:
            conn = psycopg2.connect(dsn, connect_timeout=8)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute(sql)
            cur.close()
            conn.close()
            return True, None
        except Exception as exc:
            return False, exc

    # 1. Try direct connection (works if machine has IPv6)
    direct_dsn = f"postgresql://postgres:{escaped_password}@db.{project_ref}.supabase.co:5432/postgres?sslmode=require"
    print(f"Trying db.{project_ref}.supabase.co:5432 (direct) ...")
    ok, err = try_connect_and_run(direct_dsn, "direct")
    if ok:
        print("Migration complete. Tables agent_messages and agent_registry are ready.")
        return
    print(f"  Failed: {str(err).strip().splitlines()[0]}")

    # 2. Try session-mode poolers across regions (%2E — see module docstring).
    pooler_user = f"postgres%2E{project_ref}"
    found_tenant = False
    last_error = None

    for prefix in PREFIXES:
        for region in REGIONS:
            host = f"{prefix}-{region}.pooler.supabase.com"
            dsn = f"postgresql://{pooler_user}:{escaped_password}@{host}:5432/postgres?sslmode=require"
            print(f"Trying {host}:5432 ...", end=" ", flush=True)
            ok, err = try_connect_and_run(dsn, host)
            if ok:
                print("OK")
                print("Migration complete. Tables agent_messages and agent_registry are ready.")
                print(f"Pooler: {host}")
                return
            error_line = str(err).strip().splitlines()[0]
            if "tenant" in error_line.lower() or "not found" in error_line.lower():
                print("skip")
            else:
                print(f"FAIL: {error_line}")
                found_tenant = True
                last_error = err

    print()
    if found_tenant:
        print("ERROR: Found the right pooler but authentication failed.")
        print(f"Last error: {last_error}")
        print()
        print("Check your DB password in Supabase > Settings > Database.")
        print("This is NOT the anon key or service role key — it's the Postgres password.")
    else:
        print("ERROR: Could not find a working pooler for this project.")
        print()
        print("Alternatives:")
        print("  1. Run migrations/*.sql in the Supabase SQL Editor (web UI)")
        print("  2. Check Settings > Database for the exact connection string")
    sys.exit(1)


if __name__ == "__main__":
    main()
