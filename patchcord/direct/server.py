#!/usr/bin/env python3
"""
patchcord direct mode (client talks to Supabase directly).

Gives each Claude Code session tools to send/receive messages
to/from agents on other machines via Supabase.

Repository:
  https://github.com/ppravdin/patchcord

Usage:
  AGENT_ID=frontend SUPABASE_URL=https://xxx.supabase.co SUPABASE_KEY=eyJ... python -m patchcord.direct.server
"""

import atexit
import base64
import logging
import os
import re
import socket
import sys
import time
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

from patchcord.core import (
    DEFAULT_ATTACHMENT_ALLOWED_MIME_TYPES,
    INBOX_PRECHECK_LIMIT,
    MAX_CONTENT_LENGTH,
    MCP_INSTRUCTIONS,
    STATUS_PENDING,
    STATUS_READ,
    STATUS_REPLIED,
    age_seconds,
    agent_tag,
    clean,
    err,
    format_get_attachment,
    format_inbox,
    format_list_recent_debug,
    format_recall,
    format_reply,
    format_send,
    format_upload_attachment,
    format_wait_for_message,
    full_signed_attachment_url,
    http_error,
    int_env,
    is_missing_registry_table_error,
    is_text_mime_type,
    load_dotenv,
    meta_value,
    normalize_mime_type,
    now_iso,
    presence_is_active,
    sanitize_attachment_filename,
    sanitize_attachment_segment,
    valid_agent_id,
    valid_uuid,
    validate_attachment_url,
)

# Load a project-local .env before reading required config.
# Walk up to repo root (direct/server.py lives at patchcord/direct/server.py)
_this_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(os.path.dirname(_this_dir))
load_dotenv(os.path.join(_repo_root, ".env"))
load_dotenv(os.path.join(_this_dir, ".env"))

# --- Config ---
AGENT_ID = (os.environ.get("AGENT_ID") or "").lower()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
AGENT_LABEL = os.environ.get("AGENT_LABEL") or AGENT_ID
MACHINE_NAME = os.environ.get("MACHINE_NAME") or socket.gethostname()
CLIENT_TYPE = os.environ.get("CLIENT_TYPE") or "claude_code"
AGENT_PLATFORM = os.environ.get("AGENT_PLATFORM") or sys.platform
NAMESPACE_ID = (os.environ.get("NAMESPACE_ID") or "").strip().lower()

if not NAMESPACE_ID:
    print("ERROR: NAMESPACE_ID env var is required (no default fallback — set it explicitly)", file=sys.stderr)
    sys.exit(1)

if not all([AGENT_ID, SUPABASE_URL, SUPABASE_KEY]):
    print("ERROR: Set AGENT_ID, SUPABASE_URL, SUPABASE_KEY env vars", file=sys.stderr)
    sys.exit(1)

if not re.match(r"^[a-zA-Z0-9_\-\.]{1,120}$", AGENT_ID):
    print(f"ERROR: AGENT_ID contains invalid characters or is too long: {AGENT_ID!r}", file=sys.stderr)
    sys.exit(1)

TABLE = "agent_messages"
REGISTRY_TABLE = "agent_registry"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}
BASE = f"{SUPABASE_URL}/rest/v1/{TABLE}"
REGISTRY_BASE = f"{SUPABASE_URL}/rest/v1/{REGISTRY_TABLE}"
STORAGE_BASE = f"{SUPABASE_URL}/storage/v1"
PRESENCE_PING_SECONDS = int_env("PRESENCE_PING_SECONDS", default=15, minimum=5, maximum=300)
DEFAULT_ACTIVE_WINDOW_SECONDS = 180
ATTACHMENT_MAX_BYTES = int_env(
    "PATCHCORD_ATTACHMENT_MAX_BYTES", default=10 * 1024 * 1024, minimum=1024, maximum=50 * 1024 * 1024
)
ATTACHMENT_URL_EXPIRY_SECONDS = int_env(
    "PATCHCORD_ATTACHMENT_URL_EXPIRY_SECONDS", default=86400, minimum=60, maximum=7 * 86400
)
ATTACHMENT_BUCKET = os.environ.get("PATCHCORD_ATTACHMENTS_BUCKET", "attachments").strip() or "attachments"
ATTACHMENT_ALLOWED_MIME_TYPES = DEFAULT_ATTACHMENT_ALLOWED_MIME_TYPES[:]

_log = logging.getLogger("patchcord.direct")

mcp = FastMCP("patchcord", instructions=MCP_INSTRUCTIONS)

client = httpx.Client(timeout=30)


# --- Supabase REST helpers (sync) ---


def _post(data: dict) -> dict:
    r = client.post(BASE, headers=HEADERS, json=data)
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else {}


def _get(params: dict) -> list:
    r = client.get(BASE, headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def _patch(msg_id: str, data: dict) -> dict:
    r = client.patch(
        BASE,
        headers={**HEADERS, "Prefer": "return=representation"},
        params={"id": f"eq.{msg_id}"},
        json=data,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else {}


def _delete(params: dict) -> list:
    r = client.delete(
        BASE,
        headers={**HEADERS, "Prefer": "return=representation"},
        params=params,
    )
    r.raise_for_status()
    return r.json()


def _get_registry(params: dict) -> list:
    r = client.get(REGISTRY_BASE, headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def _upsert_registry(data: dict) -> dict:
    r = client.post(
        REGISTRY_BASE,
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
        params={"on_conflict": "namespace_id,agent_id"},
        json=data,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else {}


# --- Presence ---

_registry_disabled = False
_last_presence_write = 0.0


def _set_presence(status: str = "online", note: str = "") -> dict:
    global _registry_disabled

    if _registry_disabled:
        return {}

    meta = {
        "pid": os.getpid(),
        "client_type": CLIENT_TYPE,
        "platform": AGENT_PLATFORM,
    }
    if note:
        meta["note"] = note[:200]

    payload = {
        "namespace_id": NAMESPACE_ID,
        "agent_id": AGENT_ID,
        "display_name": AGENT_LABEL,
        "machine_name": MACHINE_NAME,
        "status": status,
        "last_seen": now_iso(),
        "updated_at": now_iso(),
        "meta": meta,
    }

    try:
        return _upsert_registry(payload)
    except Exception as exc:
        if is_missing_registry_table_error(exc):
            _registry_disabled = True
        raise


def _touch_presence(force: bool = False) -> None:
    global _last_presence_write

    if _registry_disabled:
        return

    now = time.time()
    if not force and now - _last_presence_write < PRESENCE_PING_SECONDS:
        return

    try:
        _set_presence(status="online")
        _last_presence_write = now
    except Exception:
        return


def _mark_offline() -> None:
    if _registry_disabled:
        return
    try:
        _set_presence(status="offline", note="process_exit")
    except Exception:
        return


atexit.register(_mark_offline)


# --- Storage helpers (sync) ---


def _storage_request(
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    json_body: dict | None = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> httpx.Response:
    merged_headers = {**HEADERS, **(headers or {})}
    if json_body is None and content is None:
        merged_headers.pop("Content-Type", None)
    response = client.request(
        method,
        f"{STORAGE_BASE}{path}",
        params=params,
        json=json_body,
        content=content,
        headers=merged_headers,
        timeout=timeout or client.timeout,
    )
    response.raise_for_status()
    return response


_attachment_bucket_ready = False


def _ensure_attachment_bucket() -> None:
    global _attachment_bucket_ready
    if _attachment_bucket_ready:
        return
    response = _storage_request("GET", "/bucket")
    buckets = response.json()
    if isinstance(buckets, list):
        for bucket in buckets:
            if isinstance(bucket, dict) and bucket.get("id") == ATTACHMENT_BUCKET:
                _attachment_bucket_ready = True
                return
    try:
        _storage_request(
            "POST",
            "/bucket",
            json_body={
                "id": ATTACHMENT_BUCKET,
                "name": ATTACHMENT_BUCKET,
                "public": False,
                "file_size_limit": ATTACHMENT_MAX_BYTES,
                "allowed_mime_types": ATTACHMENT_ALLOWED_MIME_TYPES,
            },
        )
    except httpx.HTTPStatusError as exc:
        body = exc.response.text.lower()
        if exc.response.status_code not in (400, 409) or "already exists" not in body:
            raise
    _attachment_bucket_ready = True


def _attachment_storage_path(filename: str) -> tuple[str, str]:
    clean_ns = sanitize_attachment_segment(NAMESPACE_ID, "default")
    clean_agent = sanitize_attachment_segment(AGENT_ID, "agent")
    path = f"{clean_ns}/{clean_agent}/{int(time.time() * 1000)}_{sanitize_attachment_filename(filename)}"
    return clean_agent, path


# --- Tools ---


@mcp.tool()
def send_message(to_agent: str, content: str) -> str:
    """Send a message to another agent. Blocks when you still have unread incoming pending messages."""
    _touch_presence()

    to_agent = clean(to_agent)
    content = clean(content)
    if not to_agent:
        return err("to_agent is required")
    if not valid_agent_id(to_agent):
        return err("invalid to_agent format")
    if not content:
        return err("content is required")
    if len(content) > MAX_CONTENT_LENGTH:
        return err(f"content exceeds {MAX_CONTENT_LENGTH} characters")

    try:
        raw_pending_for_guard = _get(
            {
                "namespace_id": f"eq.{NAMESPACE_ID}",
                "to_agent": f"eq.{AGENT_ID}",
                "status": f"eq.{STATUS_PENDING}",
                "order": "created_at.asc",
                "limit": str(INBOX_PRECHECK_LIMIT),
            }
        )
        pending_for_guard = [m for m in raw_pending_for_guard if m.get("from_agent") != AGENT_ID]
    except Exception as exc:
        return err("Failed pre-send inbox check", detail=http_error(exc))

    if pending_for_guard:
        return format_send(
            {
                "status": "blocked_pending_inbox",
                "pending_total": len(pending_for_guard),
                "incoming_messages": [
                    {
                        "message_id": m.get("id"),
                        "from": m.get("from_agent"),
                        "content": m.get("content"),
                        "sent_at": m.get("created_at"),
                    }
                    for m in pending_for_guard[:5]
                ],
            }
        )

    recipient_online = None
    recipient_machine = None
    recipient_last_seen = None
    recipient_client_type = None
    recipient_platform = None

    if not _registry_disabled:
        try:
            rows = _get_registry({"namespace_id": f"eq.{NAMESPACE_ID}", "agent_id": f"eq.{to_agent}", "limit": "1"})
            if rows:
                row = rows[0]
                recipient_online = presence_is_active(row)
                recipient_machine = row.get("machine_name")
                recipient_last_seen = row.get("last_seen")
                recipient_client_type = meta_value(row, "client_type")
                recipient_platform = meta_value(row, "platform")
        except Exception:
            _log.debug("presence check failed", exc_info=True)

    try:
        result = _post(
            {
                "namespace_id": NAMESPACE_ID,
                "from_agent": AGENT_ID,
                "to_agent": to_agent,
                "content": content,
                "status": STATUS_PENDING,
            }
        )
    except Exception as exc:
        return err("Failed to send message", detail=http_error(exc))

    msg_id = result.get("id", "unknown")
    payload = {
        "status": "sent",
        "message_id": msg_id,
        "from": AGENT_ID,
        "from_tag": agent_tag(NAMESPACE_ID, AGENT_ID),
        "to": to_agent,
        "to_tag": agent_tag(NAMESPACE_ID, to_agent),
        "tip": "Use wait_for_message() to block until any agent responds.",
    }

    if recipient_online is not None:
        payload["recipient_online"] = recipient_online
        payload["recipient_last_seen"] = recipient_last_seen
        if recipient_machine:
            payload["recipient_machine"] = recipient_machine
        if recipient_client_type:
            payload["recipient_client_type"] = recipient_client_type
        if recipient_platform:
            payload["recipient_platform"] = recipient_platform
        if not recipient_online:
            payload["recipient_tip"] = (
                "Recipient is not recently active. They may need to open Claude Code and check inbox."
            )

    return format_send(payload)


@mcp.tool()
def reply(message_id: str, content: str) -> str:
    """Reply to a message. The original sender can then retrieve your reply."""
    _touch_presence()

    message_id = clean(message_id)
    content = clean(content)
    if not message_id:
        return err("message_id is required")
    if not valid_uuid(message_id):
        return err("invalid message_id format")
    if not content:
        return err("content is required")
    if len(content) > MAX_CONTENT_LENGTH:
        return err(f"content exceeds {MAX_CONTENT_LENGTH} characters")

    try:
        originals = _get({"id": f"eq.{message_id}", "limit": "1"})
    except Exception as exc:
        return err("Failed to load original message", detail=http_error(exc))
    if not originals:
        return err("message_id not found", message_id=message_id)

    original = originals[0]
    if original["to_agent"] != AGENT_ID:
        return err(
            "Cannot reply to a message not addressed to this agent",
            message_id=message_id,
            addressed_to=original["to_agent"],
        )
    if original.get("namespace_id", "default") != NAMESPACE_ID:
        return err(
            "Cannot reply to a message from a different namespace",
            message_id=message_id,
        )

    try:
        _patch(message_id, {"status": STATUS_REPLIED})
        result = _post(
            {
                "namespace_id": NAMESPACE_ID,
                "from_agent": AGENT_ID,
                "to_agent": original["from_agent"],
                "content": content,
                "reply_to": message_id,
                "status": STATUS_PENDING,
            }
        )
    except Exception as exc:
        return err("Failed to send reply", detail=http_error(exc))

    return format_reply(
        {
            "status": STATUS_REPLIED,
            "reply_id": result.get("id"),
            "to": original["from_agent"],
        }
    )


@mcp.tool()
def wait_for_message(timeout_seconds: int = 300) -> str:
    """Poll for any new incoming message. Blocks until a message arrives or timeout. Use after replying to stay responsive."""
    _touch_presence()

    if timeout_seconds < 1:
        timeout_seconds = 1
    if timeout_seconds > 3600:
        timeout_seconds = 3600

    start = time.time()
    poll_interval = 3
    consecutive_errors = 0
    max_consecutive_errors = 3

    while time.time() - start < timeout_seconds:
        _touch_presence()
        try:
            messages = _get(
                {
                    "namespace_id": f"eq.{NAMESPACE_ID}",
                    "to_agent": f"eq.{AGENT_ID}",
                    "status": f"eq.{STATUS_PENDING}",
                    "order": "created_at.asc",
                    "limit": "1",
                }
            )
            messages = [m for m in messages if m.get("from_agent") != AGENT_ID]
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            if consecutive_errors >= max_consecutive_errors:
                return err("Failed while checking for messages", detail=http_error(exc))
            time.sleep(poll_interval)
            continue

        if messages:
            msg = messages[0]
            try:
                _patch(msg["id"], {"status": STATUS_READ})
            except Exception:
                _log.debug("failed to mark message as read", exc_info=True)
            return format_wait_for_message(
                {
                    "status": "reply_received",
                    "from": msg["from_agent"],
                    "content": msg["content"],
                    "replied_at": msg["created_at"],
                    "message_id": msg["id"],
                }
            )
        time.sleep(poll_interval)

    return format_wait_for_message(
        {
            "status": "timeout",
            "message": f"No new messages after {timeout_seconds}s.",
        }
    )


@mcp.tool()
def unsend_message(message_id: str) -> str:
    """Unsend a message you sent, if the recipient has not read it yet."""
    _touch_presence()

    message_id = clean(message_id)
    if not message_id:
        return err("message_id is required")
    if not valid_uuid(message_id):
        return err("invalid message_id format")

    try:
        messages = _get({"id": f"eq.{message_id}", "limit": "1"})
    except Exception as exc:
        return err("Failed to load message", detail=http_error(exc))
    if not messages:
        return err("message_id not found", message_id=message_id)

    msg = messages[0]

    if msg.get("from_agent") != AGENT_ID:
        return err("Cannot recall a message you did not send", message_id=message_id)
    if msg.get("namespace_id", "default") != NAMESPACE_ID:
        return err("Cannot recall a message from a different namespace", message_id=message_id)

    status = msg.get("status", "")
    if status != STATUS_PENDING:
        return format_recall(
            {
                "status": "already_read",
                "message_id": message_id,
            }
        )

    try:
        _delete({"id": f"eq.{message_id}"})
    except Exception as exc:
        return err("Failed to recall message", detail=http_error(exc))

    return format_recall(
        {
            "status": "recalled",
            "message_id": message_id,
        }
    )


@mcp.tool()
def upload_attachment(
    filename: str,
    mime_type: str = "application/octet-stream",
) -> str:
    """Get a presigned upload URL. Upload the file directly to that URL via PUT — no base64 needed."""
    _touch_presence()

    filename = clean(filename)
    mime_type = normalize_mime_type(mime_type) or "application/octet-stream"
    if not filename:
        return err("filename is required")

    clean_agent, object_path = _attachment_storage_path(filename)
    encoded_path = quote(object_path, safe="/")

    try:
        _ensure_attachment_bucket()
        upload_resp = _storage_request(
            "POST",
            f"/object/upload/sign/{ATTACHMENT_BUCKET}/{encoded_path}",
        )
    except Exception as exc:
        return err("Failed to create upload URL", detail=http_error(exc))

    upload_payload = upload_resp.json()
    relative_url = upload_payload.get("url", "")
    if not relative_url:
        return err("Storage did not return an upload URL")

    upload_url = f"{SUPABASE_URL}/storage/v1{relative_url}"

    return format_upload_attachment(
        {
            "status": "ready",
            "upload_url": upload_url,
            "path": object_path,
            "mime_type": mime_type,
        }
    )


@mcp.tool()
def get_attachment(path_or_url: str) -> str:
    """Fetch an attachment by storage path or signed URL and return its content."""
    _touch_presence()

    path_or_url = clean(path_or_url)
    if not path_or_url:
        return err("path_or_url is required")

    if (
        not path_or_url.startswith("http://")
        and not path_or_url.startswith("https://")
        and not path_or_url.startswith("/")
    ):
        encoded = quote(path_or_url, safe="/")
        try:
            sign_resp = _storage_request(
                "POST",
                f"/object/sign/{ATTACHMENT_BUCKET}/{encoded}",
                json_body={"expiresIn": ATTACHMENT_URL_EXPIRY_SECONDS},
            )
        except Exception as exc:
            return err("Failed to generate download URL", detail=http_error(exc))
        sign_payload = sign_resp.json()
        signed_url = sign_payload.get("signedURL") or sign_payload.get("signedUrl")
        if not isinstance(signed_url, str) or not signed_url:
            return err("Storage did not return a signed URL")
        fetch_url = full_signed_attachment_url(signed_url, SUPABASE_URL)
    else:
        try:
            fetch_url = validate_attachment_url(path_or_url, SUPABASE_URL, ATTACHMENT_BUCKET)
        except ValueError as exc:
            return err(str(exc))

    try:
        response = client.get(fetch_url, timeout=120)
        response.raise_for_status()
    except Exception as exc:
        return err("Failed to fetch attachment", detail=http_error(exc))

    content = response.content
    if len(content) > ATTACHMENT_MAX_BYTES:
        return err("attachment exceeds maximum size", max_bytes=ATTACHMENT_MAX_BYTES, actual_bytes=len(content))

    detected_mime = normalize_mime_type(response.headers.get("content-type", "")) or "application/octet-stream"
    result: dict[str, object] = {
        "status": "ok",
        "mime_type": detected_mime,
        "bytes": len(content),
        "path": path_or_url,
    }
    if is_text_mime_type(detected_mime):
        result["encoding"] = "text"
        result["content"] = content.decode("utf-8", errors="replace")
    else:
        result["encoding"] = "base64"
        result["content_base64"] = base64.b64encode(content).decode("ascii")
    return format_get_attachment(result)


@mcp.tool()
def list_recent_debug(limit: int = 10) -> str:
    """Debug only: list recent messages (sent and received, including already-read). Do not call routinely — use inbox() instead."""
    _touch_presence()

    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100

    try:
        sent = _get(
            {
                "namespace_id": f"eq.{NAMESPACE_ID}",
                "from_agent": f"eq.{AGENT_ID}",
                "order": "created_at.desc",
                "limit": str(limit),
            }
        )
        received = _get(
            {
                "namespace_id": f"eq.{NAMESPACE_ID}",
                "to_agent": f"eq.{AGENT_ID}",
                "order": "created_at.desc",
                "limit": str(limit),
            }
        )
    except Exception as exc:
        return err("Failed to list recent messages", detail=http_error(exc))

    deduped = {}
    for msg in sent + received:
        deduped[msg["id"]] = msg
    all_msgs = sorted(deduped.values(), key=lambda x: x["created_at"], reverse=True)[:limit]
    items = [
        {
            "id": m["id"],
            "direction": "sent" if m["from_agent"] == AGENT_ID else "received",
            "other_agent": m["to_agent"] if m["from_agent"] == AGENT_ID else m["from_agent"],
            "content": m["content"][:200],
            "status": m["status"],
            "time": m["created_at"],
        }
        for m in all_msgs
    ]
    return format_list_recent_debug({"messages": items}, AGENT_ID)


@mcp.tool()
def inbox(
    active_within_seconds: int = DEFAULT_ACTIVE_WINDOW_SECONDS,
    agents_limit: int = 50,
    inbox_limit: int = 100,
    show_presence: bool = True,
) -> str:
    """One-call overview: pending inbox (all unread messages with full content), with optional online agents."""
    _touch_presence(force=True)

    if active_within_seconds < 10:
        active_within_seconds = 10
    if active_within_seconds > 86400:
        active_within_seconds = 86400
    if agents_limit < 1:
        agents_limit = 1
    if agents_limit > 200:
        agents_limit = 200
    if inbox_limit < 1:
        inbox_limit = 1
    if inbox_limit > 500:
        inbox_limit = 500

    result: dict[str, object] = {
        "status": "ok",
        "self": {
            "namespace_id": NAMESPACE_ID,
            "full_id": f"{NAMESPACE_ID}:{AGENT_ID}",
            "agent_id": AGENT_ID,
            "agent_tag": agent_tag(NAMESPACE_ID, AGENT_ID),
            "display_name": AGENT_LABEL,
            "machine_name": MACHINE_NAME,
            "client_type": CLIENT_TYPE,
            "platform": AGENT_PLATFORM,
        },
        "show_presence": show_presence,
    }
    warnings: list[str] = []

    try:
        raw_pending = _get(
            {
                "namespace_id": f"eq.{NAMESPACE_ID}",
                "to_agent": f"eq.{AGENT_ID}",
                "status": f"eq.{STATUS_PENDING}",
                "order": "created_at.asc",
                "limit": str(inbox_limit),
            }
        )
        pending = [m for m in raw_pending if m.get("from_agent") != AGENT_ID]
        pending_ids = [m["id"] for m in raw_pending]
        if pending_ids:
            try:
                id_list = ",".join(pending_ids)
                client.patch(
                    BASE,
                    headers={**HEADERS, "Prefer": "return=minimal"},
                    params={"id": f"in.({id_list})"},
                    json={"status": STATUS_READ},
                )
            except Exception:
                _log.debug("failed to mark message as read", exc_info=True)
        result["inbox"] = {
            "pending_count": len(pending),
            STATUS_PENDING: [
                {
                    "message_id": m["id"],
                    "from": m["from_agent"],
                    "from_tag": agent_tag(NAMESPACE_ID, clean(str(m.get("from_agent", "")))),
                    "content": m["content"],
                    "sent_at": m["created_at"],
                }
                for m in pending
            ],
        }
    except Exception as exc:
        warnings.append(f"inbox_error: {http_error(exc)}")

    if show_presence and _registry_disabled:
        warnings.append("agent_registry table unavailable")
    elif show_presence:
        try:
            rows = _get_registry(
                {
                    "namespace_id": f"eq.{NAMESPACE_ID}",
                    "order": "last_seen.desc",
                    "limit": str(agents_limit),
                }
            )
            agents = []
            for row in rows:
                if not presence_is_active(row, active_within_seconds):
                    continue
                agents.append(
                    {
                        "namespace_id": NAMESPACE_ID,
                        "full_id": f"{NAMESPACE_ID}:{clean(str(row.get('agent_id', '')))}",
                        "agent_id": row.get("agent_id"),
                        "agent_tag": agent_tag(NAMESPACE_ID, clean(str(row.get("agent_id", "")))),
                        "display_name": row.get("display_name") or row.get("agent_id"),
                        "machine_name": row.get("machine_name"),
                        "client_type": meta_value(row, "client_type"),
                        "platform": meta_value(row, "platform"),
                        "last_seen": row.get("last_seen"),
                        "seconds_since_seen": age_seconds(row.get("last_seen")),
                    }
                )
            result["agents"] = {
                "active_window_seconds": active_within_seconds,
                "online_count": len(agents),
                "online": agents,
            }
        except Exception as exc:
            warnings.append(f"agents_error: {http_error(exc)}")

    if warnings:
        result["warnings"] = warnings

    return format_inbox(result)


def main() -> None:
    _touch_presence(force=True)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
