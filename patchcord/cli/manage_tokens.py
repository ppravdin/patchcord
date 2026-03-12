#!/usr/bin/env python3
"""Manage bearer tokens in the Patchcord database.

Usage:
  python -m patchcord.cli.manage_tokens add [--namespace <ns>] [--label <label>] [--token <existing>] <agent_id>
  python -m patchcord.cli.manage_tokens list
  python -m patchcord.cli.manage_tokens revoke <token>

Requires SUPABASE_URL and SUPABASE_KEY env vars.
They can be exported in the shell or stored in `.env` / `.env.server`
in the current directory or repo root.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
from pathlib import Path

import httpx

from patchcord.core import load_dotenv


def _load_default_env_files() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    for base in (Path.cwd(), repo_root):
        load_dotenv(str(base / ".env"))
        load_dotenv(str(base / ".env.server"))


_load_default_env_files()


def _env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(
            f"ERROR: {name} env var is required "
            "(export it or set it in .env/.env.server)",
            file=sys.stderr,
        )
        sys.exit(1)
    return val


def _headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def cmd_add(args: list[str]) -> int:
    namespace = "default"
    label = None
    agent_id = None

    existing_token = None

    i = 0
    while i < len(args):
        if args[i] in ("--namespace", "-n") and i + 1 < len(args):
            namespace = args[i + 1].strip()
            i += 2
        elif args[i] in ("--label", "-l") and i + 1 < len(args):
            label = args[i + 1].strip()
            i += 2
        elif args[i] in ("--token", "-t") and i + 1 < len(args):
            existing_token = args[i + 1].strip()
            i += 2
        elif not args[i].startswith("-"):
            agent_id = args[i].strip()
            i += 1
        else:
            print(f"Unknown flag: {args[i]}", file=sys.stderr)
            return 1

    if not agent_id:
        print("Usage: manage_tokens add [--namespace <ns>] [--label <label>] [--token <existing>] <agent_id>")
        return 1

    url = _env("SUPABASE_URL")
    key = _env("SUPABASE_KEY")

    token = existing_token or secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    data: dict[str, object] = {
        "token_hash": token_hash,
        "namespace_id": namespace,
        "agent_id": agent_id,
    }
    if label:
        data["label"] = label

    resp = httpx.post(
        f"{url}/rest/v1/bearer_tokens",
        headers=_headers(key),
        json=data,
        timeout=15,
    )
    if resp.status_code >= 400:
        print(f"ERROR: {resp.status_code} {resp.text}", file=sys.stderr)
        return 1

    print(f"Token created for {namespace}:{agent_id}")
    print(f"  Token: {token}")
    print(f"  Hash:  {token_hash[:16]}...")
    if label:
        print(f"  Label: {label}")
    print()
    print("Save this token now — it cannot be retrieved later.")
    return 0


def cmd_list(args: list[str]) -> int:
    url = _env("SUPABASE_URL")
    key = _env("SUPABASE_KEY")

    resp = httpx.get(
        f"{url}/rest/v1/bearer_tokens",
        headers=_headers(key),
        params={"select": "token_hash,namespace_id,agent_id,label,active,created_at", "order": "created_at.desc"},
        timeout=15,
    )
    if resp.status_code >= 400:
        print(f"ERROR: {resp.status_code} {resp.text}", file=sys.stderr)
        return 1

    rows = resp.json()
    if not rows:
        print("No bearer tokens found.")
        return 0

    print(f"{'Agent':<30} {'Label':<20} {'Active':<8} {'Hash (prefix)':<20} {'Created'}")
    print("-" * 110)
    for row in rows:
        agent = f"{row['namespace_id']}:{row['agent_id']}"
        label = row.get("label") or ""
        active = "yes" if row.get("active") else "no"
        hash_prefix = row["token_hash"][:16] + "..."
        created = row.get("created_at", "?")[:19]
        print(f"{agent:<30} {label:<20} {active:<8} {hash_prefix:<20} {created}")
    return 0


def cmd_revoke(args: list[str]) -> int:
    if not args:
        print("Usage: manage_tokens revoke <token>")
        return 1

    token = args[0].strip()
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    url = _env("SUPABASE_URL")
    key = _env("SUPABASE_KEY")

    resp = httpx.patch(
        f"{url}/rest/v1/bearer_tokens",
        headers={**_headers(key), "Prefer": "return=representation"},
        params={"token_hash": f"eq.{token_hash}"},
        json={"active": False},
        timeout=15,
    )
    if resp.status_code >= 400:
        print(f"ERROR: {resp.status_code} {resp.text}", file=sys.stderr)
        return 1

    rows = resp.json()
    if not rows:
        print("Token not found (already revoked or never existed).")
        return 1

    row = rows[0]
    print(f"Revoked token for {row['namespace_id']}:{row['agent_id']}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    cmd = args[0]
    rest = args[1:]

    if cmd == "add":
        return cmd_add(rest)
    elif cmd == "list":
        return cmd_list(rest)
    elif cmd == "revoke":
        return cmd_revoke(rest)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
