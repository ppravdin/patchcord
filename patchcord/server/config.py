"""Server-specific configuration: env parsing, token loading, constants."""

from __future__ import annotations

import os
import re
import sys
from typing import Any
from urllib.parse import urlparse

from patchcord.core import (
    DEFAULT_ATTACHMENT_ALLOWED_MIME_TYPES,
    int_env,
    load_dotenv,
)

# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _split_assignments(raw: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for part in re.split(r"[\n,;]+", raw):
        item = part.strip()
        if not item:
            continue
        if "=" in item:
            left, right = item.split("=", 1)
        elif ":" in item:
            left, right = item.split(":", 1)
        else:
            raise ValueError(f"Invalid token mapping entry: {item!r}. Expected token=agent_id.")
        token = left.strip()
        agent_id = right.strip()
        if not token or not agent_id:
            raise ValueError(f"Invalid token mapping entry: {item!r}.")
        pairs.append((token, agent_id))
    return pairs


def _parse_ns_agent(raw: str) -> tuple[str, str]:
    """Parse 'ns:agent_id' or bare 'agent_id' (defaults to 'default' namespace)."""
    if ":" in raw:
        ns, agent_id = raw.split(":", 1)
        ns = ns.strip()
        agent_id = agent_id.strip()
        if not ns:
            ns = "default"
    else:
        ns = "default"
        agent_id = raw.strip()
    return ns, agent_id


def _iso_at(timestamp: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _scope_list(scope: str) -> list[str]:
    return [part for part in scope.split() if part] or ["patchcord"]


def _parse_csv_env(raw: str, default: list[str]) -> list[str]:
    if not raw.strip():
        return default[:]
    values = []
    for part in raw.split(","):
        item = part.strip()
        if item:
            values.append(item)
    return values or default[:]


# ---------------------------------------------------------------------------
# Token management — all tokens are in the database (bearer_tokens table).
# Use: python3 -m patchcord.cli.manage_tokens add/list/revoke
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# OAuth identity detection
# ---------------------------------------------------------------------------


def _load_oauth_client_map() -> dict[str, tuple[str, str]]:
    """Load explicit OAuth client_id -> (namespace_id, agent_id) from PATCHCORD_OAUTH_CLIENTS env."""
    raw = os.environ.get("PATCHCORD_OAUTH_CLIENTS", "").strip()
    if not raw:
        return {}
    mapping: dict[str, tuple[str, str]] = {}
    for client_id, raw_identity in _split_assignments(raw):
        mapping[client_id] = _parse_ns_agent(raw_identity)
    return mapping


# Known MCP client patterns: (domain_substring, agent_id).
_DEFAULT_KNOWN_CLIENTS: list[tuple[str, str]] = [
    ("claude.ai", "claudeai"),
    ("anthropic.com", "claudeai"),
    ("chatgpt.com", "chatgpt"),
    ("openai.com", "chatgpt"),
    ("gemini.google.com", "gemini"),
    ("copilot.microsoft.com", "copilot"),
    ("github.com/copilot", "copilot"),
    ("cursor.com", "cursor"),
    ("cursor.sh", "cursor"),
    ("windsurf", "windsurf"),
    ("codeium", "windsurf"),
]

# Allowed redirect URI domains for known MCP clients.
# Prevents impersonation: a client claiming to be "chatgpt" must redirect to openai.com/chatgpt.com.
_DEFAULT_KNOWN_CLIENT_ALLOWED_DOMAINS: dict[str, list[str]] = {
    "claudeai": ["claude.ai", "anthropic.com"],
    "chatgpt": ["chatgpt.com", "openai.com"],
    "gemini": ["google.com"],
    "copilot": ["microsoft.com", "github.com", "live.com"],
    "cursor": ["cursor.com", "cursor.sh"],
    "windsurf": ["windsurf.com", "codeium.com"],
}


def _load_known_oauth_clients() -> tuple[list[tuple[str, str]], dict[str, list[str]]]:
    """Build known clients list and allowed domains from defaults + env var.

    PATCHCORD_KNOWN_OAUTH_CLIENTS extends/overrides built-in defaults.
    Format: semicolon-separated ``agent_id:domain1,domain2,...`` entries.
    If an entry uses the same agent_id as a default, the default's domains
    are REPLACED entirely.
    """
    clients = list(_DEFAULT_KNOWN_CLIENTS)
    domains: dict[str, list[str]] = {k: list(v) for k, v in _DEFAULT_KNOWN_CLIENT_ALLOWED_DOMAINS.items()}

    raw = os.environ.get("PATCHCORD_KNOWN_OAUTH_CLIENTS", "").strip()
    if not raw:
        return clients, domains

    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(
                f"Invalid PATCHCORD_KNOWN_OAUTH_CLIENTS entry: {entry!r}. Expected agent_id:domain1,domain2,..."
            )
        agent_id, domain_csv = entry.split(":", 1)
        agent_id = agent_id.strip()
        if not agent_id:
            raise ValueError(f"Empty agent_id in PATCHCORD_KNOWN_OAUTH_CLIENTS entry: {entry!r}")
        entry_domains = [d.strip() for d in domain_csv.split(",") if d.strip()]
        if not entry_domains:
            raise ValueError(f"No domains specified for agent_id {agent_id!r} in PATCHCORD_KNOWN_OAUTH_CLIENTS")

        # If this agent_id already exists in defaults, remove old patterns.
        if agent_id in domains:
            clients = [(pat, aid) for pat, aid in clients if aid != agent_id]

        for domain in entry_domains:
            clients.append((domain, agent_id))
        domains[agent_id] = entry_domains

    return clients, domains


_KNOWN_CLIENTS, _KNOWN_CLIENT_ALLOWED_DOMAINS = _load_known_oauth_clients()

_OAUTH_DEFAULT_NAMESPACE = os.environ.get("PATCHCORD_OAUTH_DEFAULT_NAMESPACE", "default").strip() or "default"


def _domain_matches(hostname: str, allowed: list[str]) -> bool:
    hostname = hostname.lower()
    return any(hostname == d or hostname.endswith("." + d) for d in allowed)


def validate_known_client_redirect_uris(
    agent_id: str,
    redirect_uris: list[Any] | None,
) -> str | None:
    """Validate redirect URIs for a known client match expected domains.

    Returns error string or None if valid.
    """
    allowed = _KNOWN_CLIENT_ALLOWED_DOMAINS.get(agent_id)
    if allowed is None:
        return None
    if not redirect_uris:
        return f"Known client {agent_id!r} must provide redirect_uris"
    for uri in redirect_uris:
        parsed = urlparse(str(uri))
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return f"Invalid redirect_uri: {uri}"
        if not _domain_matches(hostname, allowed):
            return (
                f"Redirect URI domain {hostname!r} not allowed for client {agent_id!r}. Expected: {', '.join(allowed)}"
            )
    return None


def validate_client_uri_redirect_match(
    client_uri: Any | None,
    redirect_uris: list[Any] | None,
) -> str | None:
    """Verify redirect_uri domains match client_uri domain at registration.

    Returns error string or None if valid. Only applies to non-localhost URIs.
    """
    if not client_uri or not redirect_uris:
        return None
    client_parsed = urlparse(str(client_uri))
    client_host = (client_parsed.hostname or "").lower()
    if not client_host or client_host in ("localhost", "127.0.0.1", "::1"):
        return None
    # Extract base domain (last two parts)
    client_parts = client_host.rsplit(".", 2)
    client_base = ".".join(client_parts[-2:]) if len(client_parts) >= 2 else client_host
    for uri in redirect_uris:
        parsed = urlparse(str(uri))
        redir_host = (parsed.hostname or "").lower()
        if not redir_host or redir_host in ("localhost", "127.0.0.1", "::1"):
            continue
        redir_parts = redir_host.rsplit(".", 2)
        redir_base = ".".join(redir_parts[-2:]) if len(redir_parts) >= 2 else redir_host
        if redir_base != client_base:
            return f"Redirect URI domain {redir_host!r} does not match client_uri domain {client_host!r}"
    return None


def _detect_agent_from_client_info(client_info: Any) -> tuple[str | None, bool]:
    """Auto-detect agent identity from OAuth registration metadata.

    Returns (agent_id, is_known_client) where is_known_client is True
    when the client matched a pattern in _KNOWN_CLIENTS.
    Returns (None, False) when no pattern matches and no client_name is
    available -- callers must reject the registration.
    """
    hints: list[str] = []
    if client_info.redirect_uris:
        hints.extend(str(u).lower() for u in client_info.redirect_uris)
    if client_info.client_name:
        hints.append(client_info.client_name.lower())
    if client_info.client_uri:
        hints.append(str(client_info.client_uri).lower())

    combined = " ".join(hints)
    for pattern, detected_agent_id in _KNOWN_CLIENTS:
        if pattern in combined:
            return detected_agent_id, True

    if client_info.client_name:
        raw = re.sub(r"[^a-z0-9_]", "_", client_info.client_name.lower().strip())
        raw = re.sub(r"_+", "_", raw).strip("_")[:60]
        if raw:
            return raw, False

    return None, False


# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".env"))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
PATCHCORD_NAME = os.environ.get("PATCHCORD_NAME", "patchcord")
PATCHCORD_HOST = os.environ.get("PATCHCORD_HOST", "0.0.0.0")
PATCHCORD_PORT = int_env("PATCHCORD_PORT", default=8000, minimum=1, maximum=65535)
PATCHCORD_MCP_PATH = os.environ.get("PATCHCORD_MCP_PATH", "/mcp").strip() or "/mcp"
PATCHCORD_PUBLIC_URL = os.environ.get("PATCHCORD_PUBLIC_URL", f"http://localhost:{PATCHCORD_PORT}").strip()
PATCHCORD_STATELESS_HTTP = _bool_env("PATCHCORD_STATELESS_HTTP", default=True)
PATCHCORD_BEARER_PATH = os.environ.get("PATCHCORD_BEARER_PATH", "/mcp/bearer").strip() or "/mcp/bearer"
ACTIVE_WINDOW_SECONDS_DEFAULT = int_env("PATCHCORD_ACTIVE_WINDOW_SECONDS", default=3600, minimum=10, maximum=86400)
PRESENCE_WRITE_INTERVAL_SECONDS = int_env(
    "PATCHCORD_PRESENCE_WRITE_INTERVAL_SECONDS",
    default=10,
    minimum=1,
    maximum=300,
)
OAUTH_ACCESS_TOKEN_TTL_SECONDS = int_env(
    "PATCHCORD_OAUTH_ACCESS_TOKEN_TTL_SECONDS",
    default=86400,
    minimum=300,
    maximum=31536000,
)
OAUTH_REFRESH_TOKEN_TTL_SECONDS = int_env(
    "PATCHCORD_OAUTH_REFRESH_TOKEN_TTL_SECONDS",
    default=31536000,
    minimum=3600,
    maximum=315360000,
)
ATTACHMENT_MAX_BYTES = int_env(
    "PATCHCORD_ATTACHMENT_MAX_BYTES",
    default=10 * 1024 * 1024,
    minimum=1024,
    maximum=50 * 1024 * 1024,
)
ATTACHMENT_URL_EXPIRY_SECONDS = int_env(
    "PATCHCORD_ATTACHMENT_URL_EXPIRY_SECONDS",
    default=86400,
    minimum=60,
    maximum=7 * 86400,
)
ATTACHMENT_DEFAULT_NAMESPACE = os.environ.get("PATCHCORD_DEFAULT_NAMESPACE", "default").strip() or "default"
ATTACHMENT_BUCKET = os.environ.get("PATCHCORD_ATTACHMENTS_BUCKET", "attachments").strip() or "attachments"
ATTACHMENT_ALLOWED_MIME_TYPES = _parse_csv_env(
    os.environ.get("PATCHCORD_ATTACHMENT_ALLOWED_MIME_TYPES", ""),
    default=DEFAULT_ATTACHMENT_ALLOWED_MIME_TYPES,
)
CLEANUP_MAX_AGE_DAYS = int_env("PATCHCORD_CLEANUP_MAX_AGE_DAYS", default=7, minimum=1, maximum=365)
CLEANUP_INTERVAL_HOURS = int_env("PATCHCORD_CLEANUP_INTERVAL_HOURS", default=6, minimum=1, maximum=168)
RATE_LIMIT_PER_MINUTE = int_env("PATCHCORD_RATE_LIMIT_PER_MINUTE", default=100, minimum=1, maximum=10000)
ANON_RATE_LIMIT_PER_MINUTE = int_env("PATCHCORD_ANON_RATE_LIMIT_PER_MINUTE", default=20, minimum=1, maximum=1000)
RATE_BAN_SECONDS = int_env("PATCHCORD_RATE_BAN_SECONDS", default=60, minimum=1, maximum=3600)
CIRCUIT_BREAKER_SECONDS = int_env("PATCHCORD_CIRCUIT_BREAKER_SECONDS", default=300, minimum=10, maximum=3600)

if not SUPABASE_URL or not SUPABASE_KEY:
    print("ERROR: Set SUPABASE_URL and SUPABASE_KEY env vars", file=sys.stderr)
    sys.exit(1)

OAUTH_CLIENT_MAP = _load_oauth_client_map()
