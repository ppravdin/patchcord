"""Shared low-level helpers used by direct mode and the HTTP server."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx


def load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :]
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key or key in os.environ:
                    continue
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                os.environ[key] = value
    except OSError:
        return


def int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


AGENT_ID_RE = re.compile(r"^[a-zA-Z0-9_\-\.]{1,120}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
MAX_CONTENT_LENGTH = 50_000
INBOX_PRECHECK_LIMIT = 20

# Message status constants
STATUS_PENDING = "pending"
STATUS_READ = "read"
STATUS_REPLIED = "replied"
STATUS_DEFERRED = "deferred"

_SHORT_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def agent_tag(namespace_id: str, agent_id: str, length: int = 6) -> str:
    raw = f"{namespace_id}:{agent_id}".encode()
    value = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")
    chars: list[str] = []
    for _ in range(length):
        value, rem = divmod(value, 36)
        chars.append(_SHORT_ID_ALPHABET[rem])
    return "".join(reversed(chars))


def valid_agent_id(value: str) -> bool:
    return bool(AGENT_ID_RE.match(value))


def valid_uuid(value: str) -> bool:
    return bool(UUID_RE.match(value))


def to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=True)


def err(message: str, **extra: object) -> str:
    parts = [f"Error: {message}"]
    for key, val in extra.items():
        if key != "status":
            parts.append(f"  {key}: {val}")
    return "\n".join(parts)


def http_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text.strip().replace("\n", " ")
        return f"HTTP {exc.response.status_code}: {body[:300]}"
    return str(exc)


def clean(value: str) -> str:
    return value.strip() if isinstance(value, str) else ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_seconds(last_seen: object) -> int | None:
    parsed = parse_ts(last_seen)
    if parsed is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))


def relative_time(value: str | int | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        seconds = age_seconds(value)
        if seconds is None:
            return ""
    else:
        seconds = value
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def presence_is_active(row: dict[str, Any], active_within_seconds: int = 180) -> bool:
    if row.get("status") != "online":
        return False
    age = age_seconds(row.get("last_seen"))
    return age is not None and age <= active_within_seconds


def meta_value(row: dict[str, Any], key: str) -> str | None:
    meta = row.get("meta")
    if not isinstance(meta, dict):
        return None
    value = meta.get(key)
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def is_missing_registry_table_error(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    body = exc.response.text.lower()
    return "agent_registry" in body and ("does not exist" in body or "not found" in body or "relation" in body)
