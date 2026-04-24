"""Human-readable tool output formatters."""

from __future__ import annotations

import os
from typing import Any

from .helpers import STATUS_DEFERRED, STATUS_PENDING, relative_time, to_json

OUTPUT_FORMAT_JSON = os.environ.get("PATCHCORD_OUTPUT_FORMAT", "text").strip().lower() == "json"


def _json_fallback(data: dict[str, Any]) -> str | None:
    if OUTPUT_FORMAT_JSON:
        return to_json(data)
    return None


def _fmt_message(from_agent: str, sent_at: str | None, message_id: str | None, content: str) -> str:
    time_str = relative_time(sent_at)
    mid = message_id or "?"
    time_part = f" ({time_str})" if time_str else ""
    return f"From {from_agent}{time_part} [{mid}]\n  {content}"


def format_inbox(data: dict[str, Any]) -> str:
    fallback = _json_fallback(data)
    if fallback is not None:
        return fallback
    parts: list[str] = []
    self_info = data.get("self", {})
    agent_id = self_info.get("agent_id", "?")
    namespace = self_info.get("namespace_id", "")
    machine = self_info.get("machine_name", "")
    inbox_data = data.get("inbox", {})
    pending_count = inbox_data.get("pending_count", 0)
    deferred_count = inbox_data.get("deferred_count", 0)

    # Show namespace if non-default, otherwise fall back to machine name
    if namespace and namespace != "default":
        identity = f"{agent_id}@{namespace}"
    elif machine:
        identity = f"{agent_id}@{machine}"
    else:
        identity = agent_id
    summary = f"{pending_count} pending"
    if deferred_count:
        summary += f", {deferred_count} deferred"
    parts.append(f"{identity} | {summary}")

    for msg in inbox_data.get(STATUS_PENDING, []):
        parts.append("")
        parts.append(
            _fmt_message(
                msg.get("from", "?"),
                msg.get("sent_at"),
                msg.get("message_id"),
                msg.get("content", ""),
            )
        )

    if inbox_data.get(STATUS_DEFERRED):
        parts.append("")
        parts.append("Deferred:")
        for msg in inbox_data[STATUS_DEFERRED]:
            parts.append(
                _fmt_message(
                    msg.get("from", "?"),
                    msg.get("sent_at"),
                    msg.get("message_id"),
                    msg.get("content", ""),
                )
            )

    if data.get("show_presence", False):
        agents_data = data.get("agents", {})
        online = agents_data.get("online", [])
        if online:
            # Show namespace when multiple namespaces are present
            namespaces = {a.get("namespace_id", "") for a in online}
            multi_ns = len(namespaces - {""}) > 1
            names = []
            for a in online:
                aid = a.get("agent_id", "?")
                ns = a.get("namespace_id", "")
                fid = a.get("full_id", "")
                if fid.startswith("global:"):
                    names.append(aid)
                elif multi_ns and ns:
                    names.append(f"{aid}@{ns}")
                else:
                    names.append(aid)
            parts.append(f"\nOnline: {', '.join(names)}")

    for warning in data.get("warnings", []):
        parts.append(f"\nWarning: {warning}")

    return "\n".join(parts)


def format_send(data: dict[str, Any]) -> str:
    fallback = _json_fallback(data)
    if fallback is not None:
        return fallback
    status = data.get("status", "")

    if status == "blocked_pending_inbox":
        total = data.get("pending_total", 0)
        parts = [f"Send blocked — read your inbox first. {total} pending:"]
        for msg in data.get("incoming_messages", []):
            parts.append("")
            parts.append(
                _fmt_message(
                    msg.get("from", "?"),
                    msg.get("sent_at"),
                    msg.get("message_id"),
                    msg.get("content", ""),
                )
            )
        return "\n".join(parts)

    to_agent = data.get("to", "?")
    msg_id = data.get("message_id", "?")
    parts = [f"Sent to {to_agent} [{msg_id}]"]

    if data.get("recipient_online") is False:
        last_seen = relative_time(data.get("recipient_last_seen"))
        if last_seen:
            parts.append(f"  Note: {to_agent} last seen {last_seen}")
        else:
            parts.append(f"  Note: {to_agent} is offline")

    return "\n".join(parts)


def format_reply(data: dict[str, Any]) -> str:
    fallback = _json_fallback(data)
    if fallback is not None:
        return fallback
    to_agent = data.get("to", "?")
    reply_id = data.get("reply_id", "?")
    if data.get("deferred"):
        return f"Replied to {to_agent} [{reply_id}] (deferred — message stays in inbox)"
    return f"Replied to {to_agent} [{reply_id}]"


def format_recall(data: dict[str, Any]) -> str:
    fallback = _json_fallback(data)
    if fallback is not None:
        return fallback
    status = data.get("status", "")
    msg_id = data.get("message_id", "?")
    if status == "already_read":
        return f"Already read — cannot recall [{msg_id}]"
    return f"Recalled [{msg_id}]"


def format_wait_for_message(data: dict[str, Any]) -> str:
    fallback = _json_fallback(data)
    if fallback is not None:
        return fallback
    status = data.get("status", "")
    if status == "reply_received":
        from_agent = data.get("from", "?")
        time_str = relative_time(data.get("replied_at"))
        content = data.get("content", "")
        msg_id = data.get("message_id")
        time_part = f" ({time_str})" if time_str else ""
        mid_part = f" [{msg_id}]" if msg_id else ""
        return f"From {from_agent}{time_part}{mid_part}\n  {content}"
    msg = data.get("message", "No reply received.")
    return f"Timeout: {msg}"


def format_upload_attachment(data: dict[str, Any]) -> str:
    fallback = _json_fallback(data)
    if fallback is not None:
        return fallback
    path = data.get("path", "")
    mime = data.get("mime_type", "")
    if data.get("status") == "uploaded":
        size = data.get("size_bytes", 0)
        parts = [f"Uploaded ({size} bytes, {mime})"]
        parts.append(f"  Path: {path}")
        parts.append("Send the path to the other agent. They use attachment(path) to download.")
        return "\n".join(parts)
    upload_url = data.get("upload_url", "")
    parts = ["Upload ready"]
    parts.append(f"  PUT {upload_url}")
    parts.append(f"  Content-Type: {mime}")
    parts.append(f"  Path: {path}")
    parts.append("Send the path to the other agent. They use attachment(path) to download.")
    return "\n".join(parts)


def format_get_attachment(data: dict[str, Any]) -> str:
    fallback = _json_fallback(data)
    if fallback is not None:
        return fallback
    encoding = data.get("encoding", "")
    mime = data.get("mime_type", "")
    size = data.get("bytes", 0)
    path = data.get("path", "")
    if encoding == "text":
        content = data.get("content", "")
        return f"[{mime}, {size} bytes] {path}\n\n{content}"
    b64 = data.get("content_base64", "")
    return f"[{mime}, {size} bytes, base64] {path}\n\n{b64}"


def format_recall_history(data: dict[str, Any], self_agent_id: str) -> str:
    fallback = _json_fallback(data)
    if fallback is not None:
        return fallback
    messages = data.get("messages", [])
    if not messages:
        return "No recent messages."
    parts: list[str] = []
    for msg in messages:
        direction = msg.get("direction", "")
        other = msg.get("other_agent", "?")
        content = msg.get("content", "")
        status = msg.get("status", "")
        time_str = relative_time(msg.get("time"))
        msg_id = msg.get("id", "?")
        time_part = f" ({time_str})" if time_str else ""
        arrow = f"-> {other}" if direction == "sent" else f"<- {other}"
        parts.append(f"[{msg_id}] {arrow}{time_part} [{status}]")
        parts.append(f"  {content}")
    return "\n".join(parts)


def format_relay_url(data: dict[str, Any]) -> str:
    fallback = _json_fallback(data)
    if fallback is not None:
        return fallback
    status = data.get("status", "")
    if status == "fetch_failed":
        detail = data.get("detail", "unknown error")
        return f"Fetch failed: {detail}"
    path = data.get("path", "")
    size = data.get("size", 0)
    msg_id = data.get("message_id", "?")
    to_agent = data.get("to_agent", "?")
    parts = [f"Relayed to {to_agent} [{msg_id}]"]
    parts.append(f"  Path: {path}")
    if size:
        if size >= 1024 * 1024:
            parts.append(f"  Size: {size / (1024 * 1024):.1f} MB")
        else:
            parts.append(f"  Size: {size / 1024:.1f} KB")
    warning = data.get("warning")
    if warning:
        parts.append(f"  Warning: {warning}")
    return "\n".join(parts)
