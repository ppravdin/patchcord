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
    format_list_recent_debug,
    format_recall,
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
    http_client,
    ssrf_safe_client,
)

_log = logging.getLogger("patchcord.server.tools")


def register(mcp):  # noqa: C901 — registering all tools in one function
    """Register all MCP tools on the given FastMCP server instance."""

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def send_message(to_agent: str, content: str, ctx: Context) -> str:
        """Send a message to another agent. Supports agent@namespace for cross-namespace targeting."""
        try:
            namespace_id, agent_id_val = _get_current_identity(ctx)
        except Exception:
            return err("Unauthorized request")

        await _touch_presence(namespace_id, agent_id_val, ctx)

        to_agent = clean(to_agent)
        content = clean(content)
        if not to_agent:
            return err("to_agent is required")

        # Parse agent@namespace before validation
        if "@" in to_agent:
            agent_part, ns_part = to_agent.rsplit("@", 1)
            agent_part = agent_part.strip()
            ns_part = ns_part.strip()
            if not agent_part or not ns_part:
                return err("invalid agent@namespace format")
            if not valid_agent_id(agent_part):
                return err("invalid to_agent format")
        else:
            if not valid_agent_id(to_agent):
                return err("invalid to_agent format")

        if not content:
            return err("content is required")
        if len(content) > MAX_CONTENT_LENGTH:
            return err(f"content exceeds {MAX_CONTENT_LENGTH} characters")

        # Resolve target namespace (cross-namespace for OAuth agents)
        is_oauth = _is_oauth_agent(ctx)
        try:
            target_ns, to_agent_resolved = await _resolve_target_agent(namespace_id, to_agent, is_oauth)
        except ValueError as exc:
            return err(str(exc))

        # Pre-send inbox guard — OAuth agents check across all namespaces
        guard_params: dict[str, str] = {
            "to_agent": f"eq.{agent_id_val}",
            "status": f"eq.{STATUS_PENDING}",
            "order": "created_at.asc",
            "limit": str(INBOX_PRECHECK_LIMIT),
        }
        if not is_oauth:
            guard_params["namespace_id"] = f"eq.{namespace_id}"

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

        # Recipient presence check (in target namespace)
        recipient_online: bool | None = None
        recipient_machine: str | None = None
        recipient_last_seen: str | None = None
        recipient_client_type: str | None = None
        recipient_platform: str | None = None

        from patchcord.server import helpers

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

        try:
            result = await _post_message(
                {
                    "namespace_id": target_ns,
                    "from_agent": agent_id_val,
                    "to_agent": to_agent_resolved,
                    "content": content,
                    "status": msg_status,
                }
            )
        except Exception as exc:
            return err("Failed to send message", detail=http_error(exc))

        message_id = result.get("id", "unknown")
        payload: dict[str, Any] = {
            "status": "sent",
            "message_id": message_id,
            "from": agent_id_val,
            "from_tag": agent_tag(namespace_id, agent_id_val),
            "to": to_agent_resolved,
            "to_tag": agent_tag(target_ns, to_agent_resolved),
            "tip": "Use wait_for_message() to block until the other agent responds.",
        }
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
    async def reply(message_id: str, content: str, defer: bool = False, ctx: Context | None = None) -> str:
        """Reply to a message addressed to your authenticated agent.

        Set defer=true to send the reply but keep the original message in your
        inbox as "deferred." Deferred messages persist across sessions and show
        in a separate inbox section. Reply again without defer to resolve it.
        """
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
        if not content:
            return err("content is required")
        if len(content) > MAX_CONTENT_LENGTH:
            return err(f"content exceeds {MAX_CONTENT_LENGTH} characters")

        try:
            originals = await _get_messages({"id": f"eq.{message_id}", "limit": "1"})
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
        # Namespace check: token agents must match; OAuth agents can reply cross-namespace
        orig_ns = original.get("namespace_id", "default")
        if orig_ns != namespace_id and not _is_oauth_agent(ctx):
            return err(
                "Cannot reply to a message from a different namespace",
                message_id=message_id,
            )

        # Store reply in the original message's namespace (keeps reply chain together)
        reply_ns = orig_ns

        # defer=true: mark as deferred (persists in inbox); defer=false: mark as replied (resolved)
        new_status = STATUS_DEFERRED if defer else STATUS_REPLIED

        try:
            await _patch_message(message_id, {"status": new_status})
            result = await _post_message(
                {
                    "namespace_id": reply_ns,
                    "from_agent": agent_id_val,
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
                "status": new_status,
                "reply_id": result.get("id"),
                "to": original["from_agent"],
                "deferred": defer,
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
    async def unsend_message(message_id: str, ctx: Context) -> str:
        """Unsend a message you sent, if the recipient has not read it yet."""
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
        # Namespace check: token agents must match; OAuth agents can recall cross-namespace
        if msg.get("namespace_id", "default") != namespace_id and not _is_oauth_agent(ctx):
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
    async def upload_attachment(
        filename: str,
        mime_type: str = "application/octet-stream",
        ctx: Context | None = None,
    ) -> str:
        """Get a presigned upload URL. Upload the file directly to that URL via PUT — no base64 needed."""
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

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def relay_url(
        url: str,
        filename: str,
        to_agent: str,
        mime_type: str = "application/octet-stream",
        ctx: Context | None = None,
    ) -> str:
        """Fetch a URL and relay its content as an attachment to another agent.

        The server downloads the URL, stores it in Supabase Storage, and sends
        a notification message to to_agent with the attachment path.
        Useful for web-platform agents that cannot upload directly.
        """
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
        to_agent_clean = clean(to_agent)
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
            return err("filename is required")
        if not to_agent_clean:
            return err("to_agent is required")

        # Resolve target namespace (cross-namespace for OAuth agents)
        is_oauth = _is_oauth_agent(ctx)
        try:
            target_ns, to_agent_resolved = await _resolve_target_agent(namespace_id, to_agent_clean, is_oauth)
        except ValueError as exc:
            return err(str(exc))

        # --- Send gate: block if sender has unread pending messages ---
        guard_params: dict[str, str] = {
            "to_agent": f"eq.{agent_id_val}",
            "status": f"eq.{STATUS_PENDING}",
            "order": "created_at.asc",
            "limit": str(INBOX_PRECHECK_LIMIT),
        }
        if not is_oauth:
            guard_params["namespace_id"] = f"eq.{namespace_id}"
        try:
            pending_for_guard = await _get_messages(guard_params)
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

        # --- Send notification message to target agent ---
        size_kb = len(content_bytes) / 1024
        if size_kb >= 1024:
            size_str = f"{size_kb / 1024:.1f}MB"
        else:
            size_str = f"{size_kb:.0f}KB"
        notify_content = f"[file] {filename} ({size_str}) — shared by {agent_id_val}\nPath: {object_path}"
        try:
            msg_result = await _post_message(
                {
                    "namespace_id": target_ns,
                    "from_agent": agent_id_val,
                    "to_agent": to_agent_resolved,
                    "content": notify_content,
                    "status": STATUS_PENDING,
                }
            )
        except Exception as exc:
            return format_relay_url(
                {
                    "status": "relayed",
                    "path": object_path,
                    "size": len(content_bytes),
                    "to_agent": to_agent_resolved,
                    "message_id": "?",
                    "warning": f"Uploaded but notification failed: {exc}",
                }
            )

        msg_id = msg_result.get("id", "?")
        return format_relay_url(
            {
                "status": "relayed",
                "path": object_path,
                "size": len(content_bytes),
                "to_agent": to_agent_resolved,
                "message_id": msg_id,
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def get_attachment(path_or_url: str, ctx: Context | None = None) -> str:
        """Fetch an attachment by storage path or signed URL and return its content."""
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

            # Path isolation: namespace is the trust boundary.
            # Bearer-token agents: can access any path within their namespace.
            # OAuth agents: can access any path (cross-namespace by design).
            # Storage paths are namespace/agent_id/timestamp_filename.
            if not _is_oauth_agent(ctx):
                parts = path_or_url.split("/")
                path_ns = parts[0] if len(parts) > 0 else ""
                if path_ns != namespace_id:
                    return err(f"Access denied: path belongs to namespace {path_ns!r}, you are in {namespace_id!r}")
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
        """Poll for any new incoming message. Blocks until a message arrives or timeout. Use after replying to stay responsive."""
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

        is_oauth = _is_oauth_agent(ctx)

        started = time.time()
        poll_interval = 3

        while time.time() - started < timeout_seconds:
            await _touch_presence(namespace_id, agent_id_val, ctx)
            try:
                params: dict[str, str] = {
                    "to_agent": f"eq.{agent_id_val}",
                    "status": f"eq.{STATUS_PENDING}",
                    "order": "created_at.asc",
                    "limit": "1",
                }
                if not is_oauth:
                    params["namespace_id"] = f"eq.{namespace_id}"
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
            await asyncio.sleep(poll_interval)

        return format_wait_for_message(
            {
                "status": "timeout",
                "message": f"No new messages after {timeout_seconds}s.",
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def inbox(
        active_within_seconds: int = ACTIVE_WINDOW_SECONDS_DEFAULT,
        agents_limit: int = 50,
        inbox_limit: int = 100,
        show_presence: bool = True,
        ctx: Context | None = None,
    ) -> str:
        """One-call overview: pending inbox (all unread messages with full content), with optional online agents."""
        if ctx is None:
            return err("Context missing")

        try:
            namespace_id, agent_id_val = _get_current_identity(ctx)
        except Exception:
            return err("Unauthorized request")

        await _touch_presence(namespace_id, agent_id_val, ctx, force=True)

        active_within_seconds = max(10, min(86400, active_within_seconds))
        agents_limit = max(1, min(200, agents_limit))
        inbox_limit = max(1, min(500, inbox_limit))

        is_oauth = _is_oauth_agent(ctx)

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

        # Fetch pending + deferred messages; OAuth agents see all namespaces
        inbox_params: dict[str, str] = {
            "to_agent": f"eq.{agent_id_val}",
            "status": f"in.({STATUS_PENDING},{STATUS_DEFERRED})",
            "order": "created_at.asc",
            "limit": str(inbox_limit),
        }
        if not is_oauth:
            inbox_params["namespace_id"] = f"eq.{namespace_id}"

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
                return {
                    "message_id": message["id"],
                    "from": message["from_agent"],
                    "from_tag": agent_tag(
                        message.get("namespace_id", namespace_id),
                        clean(str(message.get("from_agent", ""))),
                    ),
                    "content": message["content"],
                    "sent_at": message["created_at"],
                }

            result_data["inbox"] = {
                "pending_count": len(pending),
                STATUS_PENDING: [_msg_entry(m) for m in pending],
                "deferred_count": len(deferred),
                STATUS_DEFERRED: [_msg_entry(m) for m in deferred],
            }
        except Exception as exc:
            warnings.append(f"inbox_error: {http_error(exc)}")

        from patchcord.server import helpers

        if show_presence and helpers.is_registry_disabled():
            warnings.append("agent_registry table unavailable")
        elif show_presence:
            # OAuth agents see agents across all namespaces; token agents stay scoped
            presence_params: dict[str, str] = {
                "order": "last_seen.desc",
                "limit": str(agents_limit),
            }
            if not is_oauth:
                presence_params["namespace_id"] = f"eq.{namespace_id}"
            try:
                rows = await _get_registry(presence_params)
                active_rows = []
                for row in rows:
                    if not presence_is_active(row, active_within_seconds):
                        continue
                    row_ns = row.get("namespace_id", namespace_id)
                    row_agent = clean(str(row.get("agent_id", "")))
                    active_rows.append(
                        {
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
                    )
                # Lazy discovery for token agents: show cross-namespace counterparties
                # that this agent has exchanged messages with.
                if not is_oauth:
                    try:
                        existing_ids = {r.get("agent_id") for r in active_rows}
                        counterparty_ids: set[str] = set()

                        recv_msgs = await _get_messages(
                            {
                                "namespace_id": f"eq.{namespace_id}",
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
                                "namespace_id": f"eq.{namespace_id}",
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
    async def list_recent_debug(limit: int = 10, ctx: Context | None = None) -> str:
        """Debug only: list recent messages (sent and received, including already-read). Do not call routinely — use inbox() instead."""
        if ctx is None:
            return err("Context missing")

        try:
            namespace_id, agent_id_val = _get_current_identity(ctx)
        except Exception:
            return err("Unauthorized request")

        await _touch_presence(namespace_id, agent_id_val, ctx)

        limit = max(1, min(100, limit))

        # OAuth agents see messages across all namespaces
        is_oauth = _is_oauth_agent(ctx)
        sent_params: dict[str, str] = {
            "from_agent": f"eq.{agent_id_val}",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        recv_params: dict[str, str] = {
            "to_agent": f"eq.{agent_id_val}",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        if not is_oauth:
            sent_params["namespace_id"] = f"eq.{namespace_id}"
            recv_params["namespace_id"] = f"eq.{namespace_id}"

        try:
            sent = await _get_messages(sent_params)
            received = await _get_messages(recv_params)
        except Exception as exc:
            return err("Failed to list recent messages", detail=http_error(exc))

        deduped: dict[str, dict[str, Any]] = {}
        for m in sent + received:
            deduped[m["id"]] = m
        recent = sorted(deduped.values(), key=lambda x: x["created_at"], reverse=True)[:limit]

        return format_list_recent_debug(
            {
                "messages": [
                    {
                        "id": row["id"],
                        "direction": "sent" if row["from_agent"] == agent_id_val else "received",
                        "other_agent": row["to_agent"] if row["from_agent"] == agent_id_val else row["from_agent"],
                        "content": row["content"][:200],
                        "status": row["status"],
                        "time": row["created_at"],
                    }
                    for row in recent
                ],
            },
            agent_id_val,
        )
