"""MCP tool handlers for the centralized server."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from patchcord.core import (
    INBOX_PRECHECK_LIMIT,
    MAX_CONTENT_LENGTH,
    STATUS_DEFERRED,
    STATUS_PENDING,
    STATUS_READ,
    STATUS_REPLIED,
    age_seconds,
    agent_tag,
    clean,
    err,
    format_get_attachment,
    format_inbox,
    format_recall,
    format_recall_history,
    format_relay_url,
    format_reply,
    format_send,
    format_upload_attachment,
    format_wait_for_message,
    full_signed_attachment_url,
    http_error,
    is_text_mime_type,
    meta_value,
    normalize_mime_type,
    presence_is_active,
    sanitize_attachment_filename,
    valid_agent_id,
    valid_uuid,
    validate_attachment_url,
)
from patchcord.server.config import (
    ACTIVE_WINDOW_SECONDS_DEFAULT,
    ATTACHMENT_BUCKET,
    ATTACHMENT_MAX_BYTES,
    ATTACHMENT_URL_EXPIRY_SECONDS,
    SUPABASE_URL,
)
from patchcord.server.helpers import (
    BASE,
    _agent_display_name,
    _attachment_storage_path,
    _delete_rows,
    _derive_client_type,
    _derive_machine_name,
    _derive_platform,
    _ensure_attachment_bucket,
    _get_current_identity,
    _get_messages,
    _get_registry,
    _is_oauth_agent,
    _patch_message,
    _post_message,
    _resolve_target_agent,
    _storage_request,
    _touch_presence,
    get_user_namespace_ids,
    http_client,
    namespace_ids_match,
    ssrf_safe_client,
    user_ns_filter,
)

_log = logging.getLogger("patchcord.server.tools")


async def _scoped_namespace_ids(namespace_id: str, ctx: Context) -> list[str]:
    """Return namespace list scoped by auth type.

    OAuth agents (claude.ai, chatgpt) see all of the user's namespaces.
    Token agents (Claude Code, Cursor, etc.) see only their own namespace.
    """

    if _is_oauth_agent(ctx):
        return await get_user_namespace_ids(namespace_id)
    return [namespace_id]


def register(mcp):  # noqa: C901 — registering all tools in one function
    """Register all MCP tools on the given FastMCP server instance."""

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def send_message(to_agent: str, content: str, thread: str = "", ctx: Context | None = None) -> str:
        """Send a message to an agent. Use commas for multiple recipients. Messages support up to 50,000 characters — send full content, specifications, and code as-is. Never summarize or truncate when sending. `thread` (optional slug) groups related messages as a named thread: send_message(to_agent="frontend", content="...", thread="auth-migration"). reply() auto-inherits the thread."""
        try:
            namespace_id, agent_id_val = _get_current_identity(ctx)
        except Exception:
            return err("Unauthorized request")

        await _touch_presence(namespace_id, agent_id_val, ctx)

        content = clean(content)
        if not content:
            return err("content is required")
        if len(content) > MAX_CONTENT_LENGTH:
            return err(f"content exceeds {MAX_CONTENT_LENGTH} characters")

        # Parse comma-separated recipients (dedup, preserve order)
        raw_recipients = [r.strip() for r in to_agent.split(",") if r.strip()]
        if not raw_recipients:
            return err("to_agent is required")

        # Dedup while preserving order
        seen: set[str] = set()
        recipients: list[str] = []
        for r in raw_recipients:
            r_clean = clean(r)
            if r_clean and r_clean not in seen:
                seen.add(r_clean)
                recipients.append(r_clean)

        if not recipients:
            return err("to_agent is required")

        # Validate all recipient formats
        for r in recipients:
            if "@" in r:
                agent_part, ns_part = r.rsplit("@", 1)
                if not agent_part.strip() or not ns_part.strip():
                    return err(f"invalid agent@namespace format: {r}")
                if not valid_agent_id(agent_part.strip()):
                    return err(f"invalid to_agent format: {r}")
            else:
                if not valid_agent_id(r):
                    return err(f"invalid to_agent format: {r}")

        # Pre-send inbox guard — scoped to user's namespaces
        is_oauth = _is_oauth_agent(ctx)
        user_ns = await _scoped_namespace_ids(namespace_id, ctx)
        guard_params: dict[str, str] = {
            "to_agent": f"eq.{agent_id_val}",
            "status": f"eq.{STATUS_PENDING}",
            "order": "created_at.asc",
            "limit": str(INBOX_PRECHECK_LIMIT),
            "namespace_id": user_ns_filter(user_ns),
        }

        try:
            raw_pending_for_guard = await _get_messages(guard_params)
            pending_for_guard = [m for m in raw_pending_for_guard if m.get("from_agent") != agent_id_val]
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

        thread_slug = clean(thread) if thread else ""

        # Single recipient — original path (no group_id, backward compatible)
        if len(recipients) == 1:
            return await _send_single(
                namespace_id,
                agent_id_val,
                recipients[0],
                content,
                is_oauth,
                ctx,
                thread_slug=thread_slug,
            )

        # Multi-recipient — fan-out with shared group_id
        import uuid

        group_id = str(uuid.uuid4())
        recipient_tags = [clean(r) for r in recipients]
        results: list[dict[str, Any]] = []
        sent_count = 0
        fail_count = 0

        for recipient in recipients:
            try:
                target_ns, to_agent_resolved = await _resolve_target_agent(namespace_id, recipient, is_oauth)
                result = await _post_message(
                    {
                        "namespace_id": target_ns,
                        "from_agent": agent_id_val,
                        "to_agent": to_agent_resolved,
                        "content": content,
                        "status": STATUS_PENDING,
                        "group_id": group_id,
                        "recipients": recipient_tags,
                    }
                )
                results.append(
                    {
                        "to": to_agent_resolved,
                        "to_tag": agent_tag(target_ns, to_agent_resolved),
                        "message_id": result.get("id", "unknown"),
                        "status": "sent",
                    }
                )
                sent_count += 1
            except Exception as exc:
                results.append(
                    {
                        "to": recipient,
                        "status": "failed",
                        "error": str(exc)[:200],
                    }
                )
                fail_count += 1

        payload: dict[str, Any] = {
            "status": "sent" if fail_count == 0 else "partial" if sent_count > 0 else "failed",
            "sent_count": sent_count,
            "failed_count": fail_count,
            "group_id": group_id,
            "recipients": results,
        }
        return format_send(payload)

    async def _resolve_thread_id(
        namespace_id: str,
        agent_id_val: str,
        to_agent_resolved: str,
        slug: str,
    ) -> str | None:
        """Given a thread slug, find existing thread_id or signal to create a new one.

        Returns None if slug is empty, "__new__" if a new thread root should be created,
        or the existing thread_id UUID to reuse.
        """
        if not slug:
            return None
        try:
            rows = await _get_messages(
                {
                    "namespace_id": f"eq.{namespace_id}",
                    "thread_title": f"eq.{slug}",
                    "or": f"(and(from_agent.eq.{agent_id_val},to_agent.eq.{to_agent_resolved}),and(from_agent.eq.{to_agent_resolved},to_agent.eq.{agent_id_val}))",
                    "select": "id,thread_id",
                    "limit": "1",
                    "order": "created_at.asc",
                },
            )
            if rows:
                return rows[0].get("thread_id") or rows[0]["id"]
        except Exception:
            pass
        return "__new__"

    async def _send_single(
        namespace_id: str,
        agent_id_val: str,
        to_agent: str,
        content: str,
        is_oauth: bool,
        ctx: Context | None,
        thread_slug: str = "",
    ) -> str:
        """Send a single message (original path, no group_id)."""
        try:
            target_ns, to_agent_resolved = await _resolve_target_agent(namespace_id, to_agent, is_oauth)
        except ValueError as exc:
            return err(str(exc))

        from patchcord.server import helpers

        # Recipient presence check
        recipient_online: bool | None = None
        recipient_machine: str | None = None
        recipient_last_seen: str | None = None
        recipient_client_type: str | None = None
        recipient_platform: str | None = None

        if not helpers.is_registry_disabled():
            try:
                rows = await _get_registry(
                    {"namespace_id": f"eq.{target_ns}", "agent_id": f"eq.{to_agent_resolved}", "limit": "1"}
                )
                if rows:
                    row = rows[0]
                    recipient_online = presence_is_active(row, ACTIVE_WINDOW_SECONDS_DEFAULT)
                    recipient_machine = row.get("machine_name")
                    recipient_last_seen = row.get("last_seen")
                    recipient_client_type = meta_value(row, "client_type")
                    recipient_platform = meta_value(row, "platform")
            except Exception:
                _log.debug("presence check failed", exc_info=True)

        # Self-sends auto-defer so they survive context compaction and show in inbox
        is_self_send = to_agent_resolved == agent_id_val and target_ns == namespace_id
        msg_status = STATUS_DEFERRED if is_self_send else STATUS_PENDING

        # Thread resolution
        thread_fields: dict[str, Any] = {}
        pending_thread_slug: str | None = None
        if thread_slug:
            thread_result = await _resolve_thread_id(target_ns, agent_id_val, to_agent_resolved, thread_slug)
            if thread_result == "__new__":
                pending_thread_slug = thread_slug
            elif thread_result:
                thread_fields["thread_id"] = thread_result
                # Reopen resolved thread when a new message arrives
                try:
                    await _patch_message(thread_result, {"thread_resolved_at": None})
                except Exception:
                    pass

        try:
            result = await _post_message(
                {
                    "namespace_id": target_ns,
                    "from_agent": agent_id_val,
                    "to_agent": to_agent_resolved,
                    "content": content,
                    "status": msg_status,
                    **thread_fields,
                }
            )
        except Exception as exc:
            return err("Failed to send message", detail=http_error(exc))

        # New thread root: self-reference thread_id and set title
        if pending_thread_slug and result.get("id"):
            try:
                await _patch_message(
                    result["id"],
                    {
                        "thread_id": result["id"],
                        "thread_title": pending_thread_slug,
                    },
                )
            except Exception:
                pass

        message_id = result.get("id", "unknown")
        effective_thread_id = thread_fields.get("thread_id") or (message_id if pending_thread_slug else None)
        payload: dict[str, Any] = {
            "status": "sent",
            "message_id": message_id,
            "from": agent_id_val,
            "from_tag": agent_tag(namespace_id, agent_id_val),
            "to": to_agent_resolved,
            "to_tag": agent_tag(target_ns, to_agent_resolved),
            "tip": "Use wait_for_message() to block until the other agent responds.",
        }
        if thread_slug:
            payload["thread"] = thread_slug
            payload["thread_id"] = effective_thread_id
        if target_ns != namespace_id:
            payload["cross_namespace"] = True
            payload["target_namespace"] = target_ns
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

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def reply(
        message_id: str, content: str = "", defer: bool = False, resolve: bool = False, ctx: Context | None = None
    ) -> str:
        """Reply to a message in your inbox. Automatically stays in the thread of the message you're replying to. Set defer=true to keep the message in your inbox for later. Set resolve=true to close the thread when the task is done."""
        if ctx is None:
            return err("Context missing")
        try:
            namespace_id, agent_id_val = _get_current_identity(ctx)
        except Exception:
            return err("Unauthorized request")

        await _touch_presence(namespace_id, agent_id_val, ctx)

        message_id = clean(message_id)
        content = clean(content)
        if not message_id:
            return err("message_id is required")
        if not valid_uuid(message_id):
            return err("invalid message_id format")
        if not content and not defer and not resolve:
            return err("content is required")
        if len(content) > MAX_CONTENT_LENGTH:
            return err(f"content exceeds {MAX_CONTENT_LENGTH} characters")

        try:
            originals = await _get_messages(
                {
                    "id": f"eq.{message_id}",
                    "limit": "1",
                    "select": "id,from_agent,to_agent,namespace_id,status,thread_id,thread_title,thread_resolved_at",
                }
            )
        except Exception as exc:
            return err("Failed to load original message", detail=http_error(exc))
        if not originals:
            return err("message_id not found", message_id=message_id)

        original = originals[0]
        if original.get("to_agent") != agent_id_val:
            return err(
                "Cannot reply to a message not addressed to this agent",
                message_id=message_id,
                addressed_to=original.get("to_agent"),
            )
        # Namespace check: must be within the same user's namespaces
        orig_ns = original.get("namespace_id", "default")
        user_ns = await _scoped_namespace_ids(namespace_id, ctx)
        if not namespace_ids_match(orig_ns, user_ns):
            return err(
                "Cannot reply to a message from a different namespace",
                message_id=message_id,
            )

        # Store reply in the original message's namespace (keeps reply chain together)
        reply_ns = orig_ns

        # Thread inheritance: carry forward thread_id from the message being replied to
        orig_thread_id = original.get("thread_id")
        orig_thread_title = original.get("thread_title")
        reply_thread_fields: dict[str, Any] = {}
        if orig_thread_id:
            reply_thread_fields["thread_id"] = orig_thread_id

        # defer=true: mark as deferred (persists in inbox); defer=false: mark as replied (resolved)
        new_status = STATUS_DEFERRED if defer else STATUS_REPLIED

        try:
            status_patch: dict[str, Any] = {"status": new_status}
            if resolve:
                from patchcord.core import now_iso

                resolve_root = orig_thread_id or message_id
                try:
                    await _patch_message(resolve_root, {"thread_resolved_at": now_iso()})
                    _log.info("RESOLVE stamped thread_resolved_at on root=%s", resolve_root)
                except Exception as exc:
                    _log.warning("RESOLVE failed to stamp root=%s: %s", resolve_root, exc)
            await _patch_message(message_id, status_patch)

            if not content:
                return format_reply(
                    {
                        "status": new_status,
                        "to": original["from_agent"],
                        "deferred": defer,
                        "resolved": resolve,
                        "thread_id": orig_thread_id,
                        "thread": orig_thread_title,
                    }
                )

            result = await _post_message(
                {
                    "namespace_id": reply_ns,
                    "from_agent": agent_id_val,
                    "to_agent": original["from_agent"],
                    "content": content,
                    "reply_to": message_id,
                    "status": STATUS_PENDING,
                    **reply_thread_fields,
                }
            )
        except Exception as exc:
            return err("Failed to send reply", detail=http_error(exc))

        return format_reply(
            {
                "status": new_status,
                "reply_id": result.get("id"),
                "to": original["from_agent"],
                "deferred": defer,
                "resolved": resolve,
                "thread_id": orig_thread_id,
                "thread": orig_thread_title,
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
    async def unsend(message_id: str, ctx: Context) -> str:
        """Take back a message before the recipient reads it."""
        try:
            namespace_id, agent_id_val = _get_current_identity(ctx)
        except Exception:
            return err("Unauthorized request")

        await _touch_presence(namespace_id, agent_id_val, ctx)

        message_id = clean(message_id)
        if not message_id:
            return err("message_id is required")
        if not valid_uuid(message_id):
            return err("invalid message_id format")

        try:
            messages = await _get_messages({"id": f"eq.{message_id}", "limit": "1"})
        except Exception as exc:
            return err("Failed to load message", detail=http_error(exc))
        if not messages:
            return err("message_id not found", message_id=message_id)

        msg = messages[0]

        if msg.get("from_agent") != agent_id_val:
            return err("Cannot recall a message you did not send", message_id=message_id)
        # Namespace check: must be within the same user's namespaces
        user_ns = await _scoped_namespace_ids(namespace_id, ctx)
        if not namespace_ids_match(msg.get("namespace_id", "default"), user_ns):
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
            await _delete_rows(BASE, {"id": f"eq.{message_id}"})
        except Exception as exc:
            return err("Failed to recall message", detail=http_error(exc))

        return format_recall(
            {
                "status": "recalled",
                "message_id": message_id,
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def attachment(
        path_or_url: str = "",
        relay: bool = False,
        filename: str = "",
        mime_type: str = "",
        upload: bool = False,
        file_data: str = "",
        ctx: Context | None = None,
    ) -> str:
        """Upload a file: set upload=true with filename — returns a presigned PUT URL. curl your file there, then send the path to the other agent. Download: pass path_or_url to retrieve a shared file. Relay: set relay=true with a URL to fetch and store an external file. file_data (base64) is for web agents only that cannot curl — do not use if you can run shell commands."""
        if relay and path_or_url:
            return await _relay_url_impl(path_or_url, filename, mime_type or "application/octet-stream", ctx)
        if upload and filename:
            return await _upload_impl(filename, mime_type or "application/octet-stream", file_data, ctx)
        if path_or_url:
            return await _get_attachment_impl(path_or_url, ctx)
        return err("Provide path_or_url to download, or set upload=true with filename, or set relay=true with a URL")

    async def _upload_impl(
        filename: str,
        mime_type: str = "application/octet-stream",
        file_data: str = "",
        ctx: Context | None = None,
    ) -> str:
        """Internal: handle upload mode."""
        if ctx is None:
            return err("Context missing")

        try:
            namespace_id, agent_id_val = _get_current_identity(ctx)
        except Exception:
            return err("Unauthorized request")

        await _touch_presence(namespace_id, agent_id_val, ctx)

        filename = clean(filename)
        mime_type = normalize_mime_type(mime_type) or "application/octet-stream"
        if not filename:
            return err("filename is required")

        ns_out, clean_agent_id, object_path = _attachment_storage_path(namespace_id, agent_id_val, filename)
        encoded_path = quote(object_path, safe="/")

        # Inline mode: agents pass file_data (base64), server uploads directly
        if file_data:
            file_data = file_data.strip()
            try:
                payload = base64.b64decode(file_data, validate=True)
            except Exception:
                return err("file_data is not valid base64")
            if not payload:
                return err("decoded attachment is empty")
            if len(payload) > ATTACHMENT_MAX_BYTES:
                return err("attachment exceeds maximum size", max_bytes=ATTACHMENT_MAX_BYTES, actual_bytes=len(payload))
            try:
                await _ensure_attachment_bucket()
                await _storage_request(
                    "POST",
                    f"/object/{ATTACHMENT_BUCKET}/{encoded_path}",
                    content=payload,
                    headers={"Content-Type": mime_type, "x-upsert": "false"},
                    timeout=120,
                )
            except Exception as exc:
                return err("Failed to upload attachment", detail=http_error(exc))

            return format_upload_attachment(
                {
                    "status": "uploaded",
                    "path": object_path,
                    "mime_type": mime_type,
                    "size_bytes": len(payload),
                }
            )

        # Presigned URL mode: agents upload directly
        try:
            await _ensure_attachment_bucket()
            upload_resp = await _storage_request(
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

    async def _relay_url_impl(
        url: str,
        filename: str,
        mime_type: str = "application/octet-stream",
        ctx: Context | None = None,
    ) -> str:
        """Internal: fetch a URL and store as attachment."""
        if ctx is None:
            return err("Context missing")
        try:
            namespace_id, agent_id_val = _get_current_identity(ctx)
        except Exception:
            return err("Unauthorized request")
        await _touch_presence(namespace_id, agent_id_val, ctx)

        # --- Validate inputs ---
        url = clean(url)
        filename = clean(filename)
        provided_mime = normalize_mime_type(mime_type)

        if not url:
            return err("url is required")
        if not url.startswith("https://"):
            return err("url must start with https://")
        if len(url) > 4096:
            return err("url too long")

        # SSRF protection: resolve DNS and block private/internal IPs
        import ipaddress
        import socket
        from urllib.parse import urlparse as _urlparse

        def _check_ssrf(hostname: str) -> str | None:
            """Return error message if hostname resolves to a private/internal IP, else None."""
            if not hostname:
                return "url has no hostname"
            # Block obvious hostnames first
            if hostname in ("localhost", "0.0.0.0"):
                return "url must not target localhost"
            if hostname.endswith(".local") or hostname.endswith(".internal"):
                return "url must not target internal hosts"
            # Resolve DNS and check ALL resolved IPs
            try:
                addrinfo = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
            except socket.gaierror:
                return f"DNS resolution failed for {hostname}"
            if not addrinfo:
                return f"DNS resolution returned no results for {hostname}"
            for _family, _type, _proto, _canonname, sockaddr in addrinfo:
                ip_str = sockaddr[0]
                try:
                    ip = ipaddress.ip_address(ip_str)
                    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                        return f"url resolves to private/internal IP ({ip_str})"
                except ValueError:
                    pass
            return None

        _parsed_url = _urlparse(url)
        _hostname = (_parsed_url.hostname or "").lower()
        _ssrf_err = _check_ssrf(_hostname)
        if _ssrf_err:
            return err(_ssrf_err)
        if not filename:
            # Derive filename from URL path
            from urllib.parse import urlparse as _urlparse_fn

            _url_path = _urlparse_fn(url).path
            filename = _url_path.rsplit("/", 1)[-1] if "/" in _url_path else "download"
            if not filename:
                filename = "download"

        # --- Fetch the URL using SSRF-safe client (validates IPs at connection time) ---
        try:
            fetch_resp = await ssrf_safe_client.get(url, timeout=30, follow_redirects=False)
            # Follow redirects manually, but only HTTPS and non-private targets.
            # The SSRF-safe client validates IPs at TCP connect time for each request,
            # so DNS rebinding between pre-check and connect is caught.
            _max_redirects = 5
            while fetch_resp.is_redirect and _max_redirects > 0:
                _max_redirects -= 1
                _next_url = str(fetch_resp.headers.get("location", ""))
                if not _next_url.startswith("https://"):
                    return format_relay_url(
                        {"status": "fetch_failed", "detail": f"Redirect to non-HTTPS URL blocked: {_next_url[:200]}"}
                    )
                _next_parsed = _urlparse(_next_url)
                _next_host = (_next_parsed.hostname or "").lower()
                _next_ssrf_err = _check_ssrf(_next_host)
                if _next_ssrf_err:
                    return format_relay_url({"status": "fetch_failed", "detail": f"Redirect blocked: {_next_ssrf_err}"})
                fetch_resp = await ssrf_safe_client.get(_next_url, timeout=30, follow_redirects=False)
            fetch_resp.raise_for_status()
        except Exception as exc:
            detail = http_error(exc) if isinstance(exc, httpx.HTTPStatusError) else str(exc)
            return format_relay_url({"status": "fetch_failed", "detail": detail})

        content_bytes = fetch_resp.content
        if len(content_bytes) > ATTACHMENT_MAX_BYTES:
            return format_relay_url(
                {
                    "status": "fetch_failed",
                    "detail": f"file exceeds size limit ({len(content_bytes)} bytes, max {ATTACHMENT_MAX_BYTES})",
                }
            )

        # Sniff mime type from response if not provided
        resolved_mime = provided_mime or "application/octet-stream"
        if not provided_mime:
            resp_ct = fetch_resp.headers.get("content-type", "")
            if resp_ct:
                sniffed = normalize_mime_type(resp_ct.split(";")[0])
                if sniffed:
                    resolved_mime = sniffed

        # --- Upload to Supabase Storage ---
        safe_filename = sanitize_attachment_filename(filename)
        ns_out, clean_agent_id, object_path = _attachment_storage_path(namespace_id, agent_id_val, safe_filename)
        encoded_path = quote(object_path, safe="/")

        try:
            await _ensure_attachment_bucket()
            upload_resp = await _storage_request(
                "POST",
                f"/object/upload/sign/{ATTACHMENT_BUCKET}/{encoded_path}",
            )
            upload_payload = upload_resp.json()
            signed_upload_url = upload_payload.get("url", "")
            if not signed_upload_url:
                return err("Storage did not return an upload URL")

            full_upload_url = f"{SUPABASE_URL}/storage/v1{signed_upload_url}"
            put_resp = await http_client.put(
                full_upload_url,
                content=content_bytes,
                headers={"Content-Type": resolved_mime},
                timeout=120,
            )
            put_resp.raise_for_status()
        except Exception as exc:
            return err(f"Storage upload failed: {exc}")

        return format_relay_url(
            {
                "status": "relayed",
                "path": object_path,
                "size": len(content_bytes),
            }
        )

    async def _get_attachment_impl(path_or_url: str, ctx: Context | None = None) -> str:
        """Internal: fetch an attachment by storage path or signed URL."""
        if ctx is None:
            return err("Context missing")

        try:
            namespace_id, agent_id_val = _get_current_identity(ctx)
        except Exception:
            return err("Unauthorized request")

        await _touch_presence(namespace_id, agent_id_val, ctx)

        path_or_url = clean(path_or_url)
        if not path_or_url:
            return err("path_or_url is required")

        # If it's a storage path (not a full URL), generate a signed download URL
        if (
            not path_or_url.startswith("http://")
            and not path_or_url.startswith("https://")
            and not path_or_url.startswith("/")
        ):
            # Normalize path to prevent traversal attacks (e.g. ns/agent/../other/file)
            import posixpath

            path_or_url = posixpath.normpath(path_or_url)
            if ".." in path_or_url or path_or_url.startswith("/"):
                return err("Invalid path: traversal not allowed")

            # Path isolation: user is the trust boundary.
            # Agents can access paths within any of their user's namespaces.
            # Storage paths are namespace/agent_id/timestamp_filename.
            parts = path_or_url.split("/")
            path_ns = parts[0] if len(parts) > 0 else ""
            user_ns = await _scoped_namespace_ids(namespace_id, ctx)
            if not namespace_ids_match(path_ns, user_ns):
                return err(f"Access denied: path belongs to namespace {path_ns!r}")
            encoded = quote(path_or_url, safe="/")
            try:
                sign_resp = await _storage_request(
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
            response = await http_client.get(fetch_url, timeout=120)
            response.raise_for_status()
        except Exception as exc:
            return err("Failed to fetch attachment", detail=http_error(exc))

        content = response.content
        if len(content) > ATTACHMENT_MAX_BYTES:
            return err(
                "attachment exceeds maximum size",
                max_bytes=ATTACHMENT_MAX_BYTES,
                actual_bytes=len(content),
            )

        detected_mime = normalize_mime_type(response.headers.get("content-type", "")) or "application/octet-stream"
        result_payload: dict[str, Any] = {
            "status": "ok",
            "mime_type": detected_mime,
            "bytes": len(content),
            "path": path_or_url,
        }
        if is_text_mime_type(detected_mime):
            result_payload["encoding"] = "text"
            result_payload["content"] = content.decode("utf-8", errors="replace")
        else:
            result_payload["encoding"] = "base64"
            result_payload["content_base64"] = base64.b64encode(content).decode("ascii")
        return format_get_attachment(result_payload)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
    async def wait_for_message(timeout_seconds: int = 300, ctx: Context | None = None) -> str:
        """Wait for a reply. Use after sending or replying to stay in the conversation. Default wait is 5 minutes — enough for multi-round exchanges."""
        if ctx is None:
            return err("Context missing")

        try:
            namespace_id, agent_id_val = _get_current_identity(ctx)
        except Exception:
            return err("Unauthorized request")

        await _touch_presence(namespace_id, agent_id_val, ctx)

        if timeout_seconds < 1:
            timeout_seconds = 1
        if timeout_seconds > 3600:
            timeout_seconds = 3600

        user_ns = await _scoped_namespace_ids(namespace_id, ctx)

        started = time.time()
        poll_interval = 5
        last_presence = 0.0

        while time.time() - started < timeout_seconds:
            from patchcord.server.app import is_shutting_down

            if is_shutting_down():
                return format_wait_for_message(
                    {"status": "timeout", "message": "Server restarting. Call wait_for_message() again."}
                )
            # Throttle presence writes to every 30s during long polls
            now = time.time()
            if now - last_presence >= 30:
                await _touch_presence(namespace_id, agent_id_val, ctx)
                last_presence = now
            try:
                params: dict[str, str] = {
                    "to_agent": f"eq.{agent_id_val}",
                    "status": f"eq.{STATUS_PENDING}",
                    "order": "created_at.asc",
                    "limit": "1",
                    "select": "id,from_agent,content,created_at,namespace_id",
                    "namespace_id": user_ns_filter(user_ns),
                }
                messages = await _get_messages(params)
                # Self-sends are auto-deferred, so they won't appear in pending queries here
            except Exception as exc:
                return err("Failed while checking for messages", detail=http_error(exc))

            if messages:
                msg = messages[0]
                try:
                    await _patch_message(msg["id"], {"status": STATUS_READ})
                except Exception:
                    _log.debug("failed to mark message as read", exc_info=True)
                return format_wait_for_message(
                    {
                        "status": "reply_received",
                        "from": msg.get("from_agent"),
                        "content": msg.get("content"),
                        "replied_at": msg.get("created_at"),
                        "message_id": msg.get("id"),
                    }
                )
            elapsed = int(time.time() - started)
            # Send progress to keep HTTP connection alive through proxies
            try:
                await ctx.report_progress(progress=elapsed, total=timeout_seconds)
            except Exception:
                pass
            await asyncio.sleep(poll_interval)

        return format_wait_for_message(
            {
                "status": "timeout",
                "message": f"No new messages after {timeout_seconds}s.",
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def inbox(
        all_agents: bool = False,
        ctx: Context | None = None,
    ) -> str:
        """Get all unread messages and see who's available. Call this first when you connect. Set all_agents=true to include offline agents. Messages are returned both as a flat `pending` list and as `groups` (bucketed by thread). Offline agents can still receive messages — ask the human to check their inbox."""
        if ctx is None:
            return err("Context missing")

        try:
            namespace_id, agent_id_val = _get_current_identity(ctx)
        except Exception:
            return err("Unauthorized request")

        await _touch_presence(namespace_id, agent_id_val, ctx, force=True)

        active_within_seconds = ACTIVE_WINDOW_SECONDS_DEFAULT
        agents_limit = 50
        inbox_limit = 100
        show_presence = True

        user_ns = await _scoped_namespace_ids(namespace_id, ctx)

        result_data: dict[str, Any] = {
            "status": "ok",
            "self": {
                "namespace_id": namespace_id,
                "full_id": f"{namespace_id}:{agent_id_val}",
                "agent_id": agent_id_val,
                "agent_tag": agent_tag(namespace_id, agent_id_val),
                "display_name": _agent_display_name(agent_id_val, ctx),
                "machine_name": _derive_machine_name(ctx, agent_id_val) or "",
                "client_type": _derive_client_type(ctx),
                "platform": _derive_platform(ctx),
            },
            "show_presence": show_presence,
        }
        warnings: list[str] = []

        # Fetch pending + deferred messages — scoped to user's namespaces
        inbox_params: dict[str, str] = {
            "to_agent": f"eq.{agent_id_val}",
            "status": f"in.({STATUS_PENDING},{STATUS_DEFERRED})",
            "order": "created_at.asc",
            "limit": str(inbox_limit),
            "namespace_id": user_ns_filter(user_ns),
        }

        try:
            raw_messages = await _get_messages(inbox_params)
            # Split into pending and deferred
            pending = [m for m in raw_messages if m.get("status") == STATUS_PENDING]
            deferred = [m for m in raw_messages if m.get("status") == STATUS_DEFERRED]
            # Only mark pending messages as read; deferred stay deferred
            for message in raw_messages:
                if message.get("status") == STATUS_PENDING:
                    try:
                        await _patch_message(message["id"], {"status": STATUS_READ})
                    except Exception:
                        _log.debug("failed to mark message as read", exc_info=True)

            def _msg_entry(message):
                entry: dict[str, Any] = {
                    "message_id": message["id"],
                    "from": message["from_agent"],
                    "from_tag": agent_tag(
                        message.get("namespace_id", namespace_id),
                        clean(str(message.get("from_agent", ""))),
                    ),
                    "content": message["content"],
                    "sent_at": message["created_at"],
                }
                if message.get("thread_id"):
                    entry["thread_id"] = message["thread_id"]
                    entry["thread"] = message.get("thread_title")
                    if message.get("thread_resolved_at"):
                        entry["thread_resolved_at"] = message["thread_resolved_at"]
                return entry

            # Build groups: one group per thread (null thread_id = no thread)
            groups: list[dict[str, Any]] = []
            _seen_thread_ids: dict[str | None, int] = {}
            for m in pending:
                tid = m.get("thread_id") or None
                title = m.get("thread_title") or None
                if tid not in _seen_thread_ids:
                    _seen_thread_ids[tid] = len(groups)
                    groups.append(
                        {
                            "thread_id": tid,
                            "thread_title": title,
                            "thread_resolved_at": m.get("thread_resolved_at") or None,
                            "messages": [],
                        }
                    )
                groups[_seen_thread_ids[tid]]["messages"].append(_msg_entry(m))

            result_data["inbox"] = {
                "pending_count": len(pending),
                STATUS_PENDING: [_msg_entry(m) for m in pending],
                "groups": groups,
                "deferred_count": len(deferred),
                STATUS_DEFERRED: [_msg_entry(m) for m in deferred],
            }
        except Exception as exc:
            warnings.append(f"inbox_error: {http_error(exc)}")

        from patchcord.server import helpers

        if show_presence and helpers.is_registry_disabled():
            warnings.append("agent_registry table unavailable")
        elif show_presence:
            # Presence scoped to user's namespaces
            presence_params: dict[str, str] = {
                "order": "last_seen.desc",
                "limit": str(agents_limit),
                "namespace_id": user_ns_filter(user_ns),
            }
            try:
                rows = await _get_registry(presence_params)
                active_rows = []
                for row in rows:
                    is_active = presence_is_active(row, active_within_seconds)
                    if not is_active and not all_agents:
                        continue
                    row_ns = row.get("namespace_id", namespace_id)
                    row_agent = clean(str(row.get("agent_id", "")))
                    entry = {
                        "namespace_id": row_ns,
                        "full_id": f"{row_ns}:{row_agent}",
                        "agent_id": row.get("agent_id"),
                        "agent_tag": agent_tag(row_ns, row_agent),
                        "display_name": row.get("display_name") or row.get("agent_id"),
                        "machine_name": row.get("machine_name"),
                        "client_type": meta_value(row, "client_type"),
                        "platform": meta_value(row, "platform"),
                        "last_seen": row.get("last_seen"),
                        "seconds_since_seen": age_seconds(row.get("last_seen")),
                    }
                    if all_agents:
                        entry["online"] = is_active
                    active_rows.append(entry)
                # Show cross-namespace counterparties this agent has exchanged messages with.
                try:
                    existing_ids = {r.get("agent_id") for r in active_rows}
                    counterparty_ids: set[str] = set()

                    recv_msgs = await _get_messages(
                        {
                            "namespace_id": user_ns_filter(user_ns),
                            "to_agent": f"eq.{agent_id_val}",
                            "select": "from_agent",
                            "order": "created_at.desc",
                            "limit": "50",
                        }
                    )
                    for m in recv_msgs:
                        fa = m.get("from_agent", "")
                        if fa and fa != agent_id_val and fa not in existing_ids:
                            counterparty_ids.add(fa)

                    sent_msgs = await _get_messages(
                        {
                            "namespace_id": user_ns_filter(user_ns),
                            "from_agent": f"eq.{agent_id_val}",
                            "select": "to_agent",
                            "order": "created_at.desc",
                            "limit": "50",
                        }
                    )
                    for m in sent_msgs:
                        ta = m.get("to_agent", "")
                        if ta and ta != agent_id_val and ta not in existing_ids:
                            counterparty_ids.add(ta)

                    if counterparty_ids:
                        ids_str = ",".join(sorted(counterparty_ids))
                        cross_rows = await _get_registry(
                            {
                                "agent_id": f"in.({ids_str})",
                                "namespace_id": user_ns_filter(user_ns),
                                "order": "last_seen.desc",
                                "limit": str(min(len(counterparty_ids) * 2, 50)),
                            }
                        )
                        for crow in cross_rows:
                            if not presence_is_active(crow, active_within_seconds):
                                continue
                            crow_ns = crow.get("namespace_id", namespace_id)
                            crow_agent = clean(str(crow.get("agent_id", "")))
                            if crow_agent in existing_ids:
                                continue
                            existing_ids.add(crow_agent)
                            active_rows.append(
                                {
                                    "namespace_id": crow_ns,
                                    "full_id": f"{crow_ns}:{crow_agent}",
                                    "agent_id": crow.get("agent_id"),
                                    "agent_tag": agent_tag(crow_ns, crow_agent),
                                    "display_name": crow.get("display_name") or crow.get("agent_id"),
                                    "machine_name": crow.get("machine_name"),
                                    "client_type": meta_value(crow, "client_type"),
                                    "platform": meta_value(crow, "platform"),
                                    "last_seen": crow.get("last_seen"),
                                    "seconds_since_seen": age_seconds(crow.get("last_seen")),
                                }
                            )
                except Exception:
                    _log.debug("cross-namespace discovery failed", exc_info=True)

                result_data["agents"] = {
                    "active_window_seconds": active_within_seconds,
                    "online_count": len(active_rows),
                    "online": active_rows,
                }
            except Exception as exc:
                warnings.append(f"agents_error: {http_error(exc)}")

        if warnings:
            result_data["warnings"] = warnings
        return format_inbox(result_data)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def recall(limit: int = 10, from_agent: str = "", thread_id: str = "", ctx: Context | None = None) -> str:
        """View recent message history, including messages already read. Pass thread_id to filter to a specific thread."""
        if ctx is None:
            return err("Context missing")

        try:
            namespace_id, agent_id_val = _get_current_identity(ctx)
        except Exception:
            return err("Unauthorized request")

        await _touch_presence(namespace_id, agent_id_val, ctx)

        limit = max(1, min(100, limit))
        from_agent_clean = clean(from_agent)
        thread_id_clean = clean(thread_id)
        if thread_id_clean and not valid_uuid(thread_id_clean):
            return err("invalid thread_id format")

        # Scoped to user's namespaces
        user_ns = await _scoped_namespace_ids(namespace_id, ctx)
        ns_flt = user_ns_filter(user_ns)
        sel = "id,from_agent,to_agent,content,status,created_at,thread_id,thread_title,thread_resolved_at"
        sent_params: dict[str, str] = {
            "from_agent": f"eq.{agent_id_val}",
            "order": "created_at.desc",
            "limit": str(limit),
            "select": sel,
            "namespace_id": ns_flt,
        }
        recv_params: dict[str, str] = {
            "to_agent": f"eq.{agent_id_val}",
            "order": "created_at.desc",
            "limit": str(limit),
            "select": sel,
            "namespace_id": ns_flt,
        }
        if from_agent_clean:
            sent_params["to_agent"] = f"eq.{from_agent_clean}"
            recv_params["from_agent"] = f"eq.{from_agent_clean}"
        if thread_id_clean:
            sent_params["thread_id"] = f"eq.{thread_id_clean}"
            recv_params["thread_id"] = f"eq.{thread_id_clean}"

        try:
            sent = await _get_messages(sent_params)
            received = await _get_messages(recv_params)
        except Exception as exc:
            return err("Failed to list recent messages", detail=http_error(exc))

        deduped: dict[str, dict[str, Any]] = {}
        for m in sent + received:
            deduped[m["id"]] = m
        recent = sorted(deduped.values(), key=lambda x: x["created_at"], reverse=True)[:limit]

        def _recall_entry(row: dict) -> dict:
            entry: dict[str, Any] = {
                "id": row["id"],
                "direction": "sent" if row["from_agent"] == agent_id_val else "received",
                "other_agent": row["to_agent"] if row["from_agent"] == agent_id_val else row["from_agent"],
                "content": row["content"][:200],
                "status": row["status"],
                "time": row["created_at"],
            }
            if row.get("thread_id"):
                entry["thread_id"] = row["thread_id"]
                entry["thread"] = row.get("thread_title")
            return entry

        return format_recall_history(
            {"messages": [_recall_entry(row) for row in recent]},
            agent_id_val,
        )
