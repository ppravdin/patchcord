"""Attachment and MIME helpers shared across Patchcord modes."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

ATTACHMENT_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9._-]+")

DEFAULT_ATTACHMENT_ALLOWED_MIME_TYPES = [
    "text/*",
    "image/*",
    "application/json",
    "application/pdf",
    "application/xml",
    "application/zip",
    "application/gzip",
    "application/x-gzip",
    "application/x-tar",
    "application/octet-stream",
]


def normalize_mime_type(value: str) -> str:
    return value.strip().lower().split(";", 1)[0]


def mime_type_allowed(mime_type: str, allowed: list[str]) -> bool:
    normalized = normalize_mime_type(mime_type)
    if not normalized:
        return False
    for pattern in allowed:
        candidate = normalize_mime_type(pattern)
        if not candidate:
            continue
        if candidate.endswith("/*") and normalized.startswith(candidate[:-1]):
            return True
        if normalized == candidate:
            return True
    return False


def is_text_mime_type(mime_type: str) -> bool:
    normalized = normalize_mime_type(mime_type)
    return normalized.startswith("text/") or normalized in {
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
        "application/javascript",
    }


def sanitize_attachment_segment(value: str, fallback: str) -> str:
    cleaned = ATTACHMENT_SEGMENT_RE.sub("_", value.strip()).strip("._-")
    return cleaned[:120] or fallback


def sanitize_attachment_filename(value: str) -> str:
    collapsed = value.replace("\\", "/").split("/")[-1]
    cleaned = ATTACHMENT_SEGMENT_RE.sub("_", collapsed.strip()).strip("._-")
    return cleaned[:180] or "attachment"


def full_signed_attachment_url(url: str, supabase_url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if not url.startswith("/"):
        raise ValueError("Signed URL must be absolute or start with '/'.")
    if url.startswith("/storage/v1/"):
        return f"{supabase_url}{url}"
    return f"{supabase_url}/storage/v1{url}"


def validate_attachment_url(url: str, supabase_url: str, bucket: str) -> str:
    full_url = full_signed_attachment_url(url, supabase_url)
    parsed = urlsplit(full_url)
    supabase = urlsplit(supabase_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Attachment URL must use http or https.")
    if parsed.netloc != supabase.netloc:
        raise ValueError("Attachment URL host does not match configured Supabase host.")
    expected_prefix = f"/storage/v1/object/sign/{bucket}/"
    if not parsed.path.startswith(expected_prefix):
        raise ValueError("Attachment URL is not a signed URL for the configured attachments bucket.")
    if not parsed.query:
        raise ValueError("Attachment URL is missing its signature token.")
    return full_url
