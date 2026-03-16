"""Internal helpers: Supabase REST, storage, presence, context, cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp import Context
from starlette.requests import Request

from patchcord.core import (
    clean,
    is_missing_registry_table_error,
    now_iso,
    parse_ts,
    sanitize_attachment_filename,
    sanitize_attachment_segment,
)
from patchcord.server.config import (
    _OAUTH_DEFAULT_NAMESPACE,
    ATTACHMENT_ALLOWED_MIME_TYPES,
    ATTACHMENT_BUCKET,
    ATTACHMENT_DEFAULT_NAMESPACE,
    ATTACHMENT_MAX_BYTES,
    CIRCUIT_BREAKER_SECONDS,
    CLEANUP_MAX_AGE_DAYS,
    PATCHCORD_MCP_PATH,
    PRESENCE_WRITE_INTERVAL_SECONDS,
    SUPABASE_KEY,
    SUPABASE_URL,
)

# ---------------------------------------------------------------------------
# Supabase REST (async)
# ---------------------------------------------------------------------------

TABLE = "agent_messages"
REGISTRY_TABLE = "agent_registry"
OAUTH_CLIENTS_TABLE = "oauth_clients"
OAUTH_AUTH_CODES_TABLE = "oauth_auth_codes"
OAUTH_ACCESS_TOKENS_TABLE = "oauth_access_tokens"
OAUTH_REFRESH_TOKENS_TABLE = "oauth_refresh_tokens"
BEARER_TOKENS_TABLE = "bearer_tokens"
USER_NAMESPACES_TABLE = "user_namespaces"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
    "Cache-Control": "no-cache",
}
BASE = f"{SUPABASE_URL}/rest/v1/{TABLE}"
REGISTRY_BASE = f"{SUPABASE_URL}/rest/v1/{REGISTRY_TABLE}"
OAUTH_CLIENTS_BASE = f"{SUPABASE_URL}/rest/v1/{OAUTH_CLIENTS_TABLE}"
OAUTH_AUTH_CODES_BASE = f"{SUPABASE_URL}/rest/v1/{OAUTH_AUTH_CODES_TABLE}"
OAUTH_ACCESS_TOKENS_BASE = f"{SUPABASE_URL}/rest/v1/{OAUTH_ACCESS_TOKENS_TABLE}"
OAUTH_REFRESH_TOKENS_BASE = f"{SUPABASE_URL}/rest/v1/{OAUTH_REFRESH_TOKENS_TABLE}"
BEARER_TOKENS_BASE = f"{SUPABASE_URL}/rest/v1/{BEARER_TOKENS_TABLE}"
USER_NAMESPACES_BASE = f"{SUPABASE_URL}/rest/v1/{USER_NAMESPACES_TABLE}"
STORAGE_BASE = f"{SUPABASE_URL}/storage/v1"

http_client = httpx.AsyncClient(timeout=30)


# ---------------------------------------------------------------------------
# SSRF-safe HTTP client for external URL fetching (relay_url)
# ---------------------------------------------------------------------------


def _create_ssrf_safe_client() -> httpx.AsyncClient:
    """Create an httpx client that validates DNS at TCP connection time.

    Prevents DNS rebinding TOCTOU: the IP is validated at the moment the
    TCP socket is opened, not in a separate pre-check that could see
    different DNS results.
    """
    import ipaddress
    import socket
    import typing

    import httpcore

    class _SSRFSafeBackend(httpcore.AsyncNetworkBackend):
        """Network backend that validates resolved IPs before connecting."""

        def __init__(self, backend: httpcore.AsyncNetworkBackend):
            self._backend = backend

        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: typing.Iterable[tuple] | None = None,
        ) -> httpcore.AsyncNetworkStream:
            # Resolve DNS ourselves and validate ALL IPs
            try:
                addrinfo = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            except socket.gaierror as exc:
                raise httpcore.ConnectError(f"DNS resolution failed for {host}: {exc}") from exc

            if not addrinfo:
                raise httpcore.ConnectError(f"DNS resolution returned no results for {host}")

            validated_ip = None
            for _family, _type, _proto, _canonname, sockaddr in addrinfo:
                ip_str = sockaddr[0]
                try:
                    ip = ipaddress.ip_address(ip_str)
                    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                        raise httpcore.ConnectError(f"URL resolves to private/internal IP ({ip_str})")
                except ValueError:
                    continue
                if validated_ip is None:
                    validated_ip = ip_str

            if validated_ip is None:
                raise httpcore.ConnectError(f"No valid public IPs found for {host}")

            # Connect to validated IP directly — no re-resolution
            return await self._backend.connect_tcp(
                validated_ip,
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )

        async def connect_unix_socket(self, path, timeout=None, socket_options=None):
            raise httpcore.ConnectError("Unix socket connections not allowed for external fetches")

        async def sleep(self, seconds: float) -> None:
            await self._backend.sleep(seconds)

    backend = _SSRFSafeBackend(httpcore.AnyIOBackend())
    pool = httpcore.AsyncConnectionPool(network_backend=backend)
    transport = httpx.AsyncHTTPTransport()
    transport._pool = pool  # noqa: SLF001 — inject SSRF-safe pool
    return httpx.AsyncClient(transport=transport, timeout=30)


ssrf_safe_client = _create_ssrf_safe_client()

# ---------------------------------------------------------------------------
# Mutable state
# ---------------------------------------------------------------------------

_oauth_storage_disabled_until: float = 0.0
_registry_disabled_until: float = 0.0
_bearer_tokens_disabled_until: float = 0.0
_attachment_bucket_ready = False
_attachment_bucket_lock = asyncio.Lock()
_last_presence_write: dict[str, float] = {}

# Bearer token cache: token_hash → (namespace_id, agent_id)
# Loaded from DB on first lookup, then cached in memory.
_bearer_token_cache: dict[str, tuple[str, str]] = {}
_bearer_token_cache_loaded = False

_log = logging.getLogger("patchcord.server.helpers")


def is_oauth_storage_disabled() -> bool:
    global _oauth_storage_disabled_until
    if _oauth_storage_disabled_until == 0.0:
        return False
    if time.monotonic() >= _oauth_storage_disabled_until:
        _oauth_storage_disabled_until = 0.0
        _log.warning("oauth storage circuit breaker reset — retrying DB access")
        return False
    return True


def disable_oauth_storage() -> None:
    global _oauth_storage_disabled_until
    _oauth_storage_disabled_until = time.monotonic() + CIRCUIT_BREAKER_SECONDS
    _log.warning(
        "oauth storage disabled for %ds (circuit breaker)",
        CIRCUIT_BREAKER_SECONDS,
    )


def is_registry_disabled() -> bool:
    global _registry_disabled_until
    if _registry_disabled_until == 0.0:
        return False
    if time.monotonic() >= _registry_disabled_until:
        _registry_disabled_until = 0.0
        _log.warning("registry circuit breaker reset — retrying DB access")
        return False
    return True


def _disable_registry() -> None:
    global _registry_disabled_until
    _registry_disabled_until = time.monotonic() + CIRCUIT_BREAKER_SECONDS
    _log.warning(
        "registry disabled for %ds (circuit breaker)",
        CIRCUIT_BREAKER_SECONDS,
    )


def _is_bearer_tokens_disabled() -> bool:
    global _bearer_tokens_disabled_until
    if _bearer_tokens_disabled_until == 0.0:
        return False
    if time.monotonic() >= _bearer_tokens_disabled_until:
        _bearer_tokens_disabled_until = 0.0
        _log.warning("bearer tokens circuit breaker reset — retrying DB access")
        return False
    return True


def _disable_bearer_tokens() -> None:
    global _bearer_tokens_disabled_until
    _bearer_tokens_disabled_until = time.monotonic() + CIRCUIT_BREAKER_SECONDS
    _log.warning(
        "bearer_tokens table disabled for %ds (circuit breaker)",
        CIRCUIT_BREAKER_SECONDS,
    )


# ---------------------------------------------------------------------------
# Bearer token DB helpers
# ---------------------------------------------------------------------------


def _hash_bearer_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _load_bearer_token_cache() -> None:
    """Load all active bearer tokens from DB into memory cache. Called once."""
    global _bearer_token_cache_loaded
    if _bearer_token_cache_loaded or _is_bearer_tokens_disabled():
        return
    try:
        rows = await _get_rows(
            BEARER_TOKENS_BASE,
            {"active": "eq.true", "select": "token_hash,namespace_id,agent_id"},
        )
        for row in rows:
            token_hash = row.get("token_hash", "")
            ns = row.get("namespace_id", "default").lower()
            agent = row.get("agent_id", "").lower()
            if token_hash and agent:
                _bearer_token_cache[token_hash] = (ns, agent)
        _log.info("loaded %d bearer tokens from DB", len(_bearer_token_cache))
    except Exception as exc:
        body = ""
        if isinstance(exc, httpx.HTTPStatusError):
            body = exc.response.text.lower()
        if "bearer_tokens" in body and ("does not exist" in body or "not found" in body or "relation" in body):
            _disable_bearer_tokens()
            _log.warning("bearer_tokens table not found — DB tokens disabled (circuit breaker)")
        else:
            _log.warning("failed to load bearer tokens from DB: %s", exc)
    finally:
        _bearer_token_cache_loaded = True


async def lookup_bearer_token(token: str) -> tuple[str, str] | None:
    """Look up a bearer token, checking DB cache. Returns (namespace_id, agent_id) or None."""
    if _is_bearer_tokens_disabled():
        return None
    if not _bearer_token_cache_loaded:
        await _load_bearer_token_cache()
    token_hash = _hash_bearer_token(token)
    cached = _bearer_token_cache.get(token_hash)
    if cached is not None:
        return cached
    # Cache miss — token may have been added after boot. Check DB directly.
    try:
        rows = await _get_rows(
            BEARER_TOKENS_BASE,
            {"token_hash": f"eq.{token_hash}", "active": "eq.true", "select": "namespace_id,agent_id", "limit": "1"},
        )
        if rows:
            ns = rows[0].get("namespace_id", "default").lower()
            agent = rows[0].get("agent_id", "").lower()
            if agent:
                _bearer_token_cache[token_hash] = (ns, agent)
                return (ns, agent)
    except Exception:
        _log.debug("bearer token DB lookup failed", exc_info=True)
    return None


async def insert_bearer_token(
    token: str,
    namespace_id: str,
    agent_id: str,
    label: str | None = None,
) -> None:
    """Insert a new bearer token into the DB and update the cache."""
    token_hash = _hash_bearer_token(token)
    data: dict[str, Any] = {
        "token_hash": token_hash,
        "namespace_id": namespace_id,
        "agent_id": agent_id,
    }
    if label:
        data["label"] = label
    await _post_rows(BEARER_TOKENS_BASE, data)
    _bearer_token_cache[token_hash] = (namespace_id, agent_id)
    _log.info("inserted bearer token for %s:%s (label=%s)", namespace_id, agent_id, label)


async def deactivate_bearer_token(token: str) -> bool:
    """Deactivate a bearer token. Returns True if found."""
    token_hash = _hash_bearer_token(token)
    try:
        resp = await http_client.patch(
            BEARER_TOKENS_BASE,
            headers={**HEADERS, "Prefer": "return=representation"},
            params={"token_hash": f"eq.{token_hash}"},
            json={"active": False},
        )
        resp.raise_for_status()
        rows = resp.json()
        _bearer_token_cache.pop(token_hash, None)
        return bool(rows)
    except Exception as exc:
        _log.warning("failed to deactivate bearer token: %s", exc)
        return False


# ---------------------------------------------------------------------------
# User namespace mapping — determines which namespaces belong to the same user
# ---------------------------------------------------------------------------

# Cache: namespace_id → list of all namespace_ids owned by the same user
_user_ns_cache: dict[str, list[str]] = {}
_user_ns_cache_loaded = False
_user_ns_disabled_until: float = 0.0


def _is_user_ns_disabled() -> bool:
    global _user_ns_disabled_until
    if _user_ns_disabled_until == 0.0:
        return False
    if time.monotonic() >= _user_ns_disabled_until:
        _user_ns_disabled_until = 0.0
        _log.warning("user_namespaces circuit breaker reset — retrying")
        return False
    return True


def _disable_user_ns() -> None:
    global _user_ns_disabled_until
    _user_ns_disabled_until = time.monotonic() + CIRCUIT_BREAKER_SECONDS
    _log.warning("user_namespaces table disabled for %ds (circuit breaker)", CIRCUIT_BREAKER_SECONDS)


async def _load_user_ns_cache() -> None:
    """Load the full user_namespaces table into memory. Called once."""
    global _user_ns_cache_loaded
    if _user_ns_cache_loaded or _is_user_ns_disabled():
        return
    try:
        rows = await _get_rows(
            USER_NAMESPACES_BASE,
            {"select": "user_id,namespace_id"},
        )
        # Build user_id → [namespace_ids] mapping
        user_to_ns: dict[str, list[str]] = {}
        for row in rows:
            uid = row.get("user_id", "")
            ns = row.get("namespace_id", "")
            if uid and ns:
                user_to_ns.setdefault(uid, []).append(ns)
        # Build namespace_id → [all sibling namespace_ids] cache
        _user_ns_cache.clear()
        for _uid, ns_list in user_to_ns.items():
            for ns in ns_list:
                _user_ns_cache[ns] = ns_list
        _log.info("loaded user_namespaces: %d users, %d namespaces", len(user_to_ns), len(_user_ns_cache))
    except Exception as exc:
        body = ""
        if isinstance(exc, httpx.HTTPStatusError):
            body = exc.response.text.lower()
        if "user_namespaces" in body and (
            "does not exist" in body or "not found" in body or "relation" in body or "schema cache" in body
        ):
            _disable_user_ns()
            _log.warning("user_namespaces table not found — user isolation disabled (circuit breaker)")
        else:
            _log.warning("failed to load user_namespaces: %s", exc)
    finally:
        _user_ns_cache_loaded = True


async def get_user_namespace_ids(namespace_id: str) -> list[str]:
    """Return all namespace_ids owned by the same user who owns *namespace_id*.

    If the user_namespaces table is unavailable or the namespace isn't mapped,
    returns [namespace_id] (single-namespace fallback — no cross-namespace access).
    """
    if _is_user_ns_disabled():
        return [namespace_id]
    if not _user_ns_cache_loaded:
        await _load_user_ns_cache()
    return _user_ns_cache.get(namespace_id, [namespace_id])


def user_ns_filter(namespace_ids: list[str]) -> str:
    """Build a PostgREST namespace_id filter for a list of namespace_ids.

    Single namespace: 'eq.myns'
    Multiple: 'in.(ns1,ns2,ns3)'
    """
    if len(namespace_ids) == 1:
        return f"eq.{namespace_ids[0]}"
    return f"in.({','.join(namespace_ids)})"


def namespace_ids_match(ns: str, user_ns_list: list[str]) -> bool:
    """Check if a namespace belongs to the user's set."""
    return ns in user_ns_list


# ---------------------------------------------------------------------------
# Low-level REST helpers
# ---------------------------------------------------------------------------


def _is_missing_oauth_table_error(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    body = exc.response.text.lower()
    markers = (
        OAUTH_CLIENTS_TABLE,
        OAUTH_AUTH_CODES_TABLE,
        OAUTH_ACCESS_TOKENS_TABLE,
        OAUTH_REFRESH_TOKENS_TABLE,
    )
    return any(marker in body for marker in markers) and (
        "does not exist" in body or "not found" in body or "relation" in body or "schema cache" in body
    )


async def _get_rows(base: str, params: dict[str, str]) -> list[dict[str, Any]]:
    response = await http_client.get(base, headers=HEADERS, params=params)
    response.raise_for_status()
    return response.json()


async def _post_rows(
    base: str,
    data: dict[str, Any],
    *,
    params: dict[str, str] | None = None,
    prefer: str = "return=representation",
) -> list[dict[str, Any]]:
    response = await http_client.post(
        base,
        headers={**HEADERS, "Prefer": prefer},
        params=params,
        json=data,
    )
    response.raise_for_status()
    return response.json()


async def _delete_rows(base: str, params: dict[str, str]) -> list[dict[str, Any]]:
    response = await http_client.delete(
        base,
        headers={**HEADERS, "Prefer": "return=representation"},
        params=params,
    )
    response.raise_for_status()
    return response.json()


# --- Messages ---


async def _post_message(data: dict[str, Any]) -> dict[str, Any]:
    response = await http_client.post(BASE, headers=HEADERS, json=data)
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else {}


async def _get_messages(params: dict[str, str]) -> list[dict[str, Any]]:
    # Cache-bust header to force fresh read through any proxy/pooler layer
    headers = {**HEADERS, "X-Request-Id": f"{time.time()}"}
    response = await http_client.get(BASE, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


async def _patch_message(message_id: str, data: dict[str, Any]) -> dict[str, Any]:
    response = await http_client.patch(
        BASE,
        headers={**HEADERS, "Prefer": "return=representation"},
        params={"id": f"eq.{message_id}"},
        json=data,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else {}


# --- Registry ---


async def _get_registry(params: dict[str, str]) -> list[dict[str, Any]]:
    headers = {**HEADERS, "X-Request-Id": f"{time.time()}"}
    response = await http_client.get(REGISTRY_BASE, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


async def _upsert_registry(data: dict[str, Any]) -> dict[str, Any]:
    response = await http_client.post(
        REGISTRY_BASE,
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
        params={"on_conflict": "namespace_id,agent_id"},
        json=data,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else {}


# --- Storage ---


def _attachment_storage_path(namespace_id: str, agent_id_val: str, filename: str) -> tuple[str, str, str]:
    clean_namespace_id = sanitize_attachment_segment(namespace_id, ATTACHMENT_DEFAULT_NAMESPACE)
    clean_agent_id = sanitize_attachment_segment(agent_id_val, "agent")
    path = "/".join(
        [
            clean_namespace_id,
            clean_agent_id,
            f"{int(time.time() * 1000)}_{sanitize_attachment_filename(filename)}",
        ]
    )
    return clean_namespace_id, clean_agent_id, path


async def _storage_request(
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> httpx.Response:
    merged_headers = {**HEADERS, **(headers or {})}
    if json_body is None and content is None:
        merged_headers.pop("Content-Type", None)
    response = await http_client.request(
        method,
        f"{STORAGE_BASE}{path}",
        params=params,
        json=json_body,
        content=content,
        headers=merged_headers,
        timeout=timeout or http_client.timeout,
    )
    response.raise_for_status()
    return response


async def _ensure_attachment_bucket() -> None:
    global _attachment_bucket_ready
    if _attachment_bucket_ready:
        return

    async with _attachment_bucket_lock:
        if _attachment_bucket_ready:
            return

        response = await _storage_request("GET", "/bucket")
        buckets = response.json()
        if isinstance(buckets, list):
            for bucket in buckets:
                if isinstance(bucket, dict) and bucket.get("id") == ATTACHMENT_BUCKET:
                    _attachment_bucket_ready = True
                    return

        try:
            await _storage_request(
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


# --- OAuth storage ---


async def _oauth_get_client_row(client_id: str) -> dict[str, Any] | None:
    rows = await _get_rows(
        OAUTH_CLIENTS_BASE,
        {"client_id": f"eq.{client_id}", "limit": "1"},
    )
    return rows[0] if rows else None


async def _oauth_upsert_client_row(data: dict[str, Any]) -> dict[str, Any]:
    rows = await _post_rows(
        OAUTH_CLIENTS_BASE,
        data,
        params={"on_conflict": "client_id"},
        prefer="resolution=merge-duplicates,return=representation",
    )
    return rows[0] if rows else {}


async def _oauth_insert_auth_code_row(code: str, stored: Any) -> dict[str, Any]:
    from patchcord.server.config import _iso_at

    rows = await _post_rows(
        OAUTH_AUTH_CODES_BASE,
        {
            "code": code,
            "client_id": stored.client_id,
            "namespace_id": stored.namespace_id,
            "code_challenge": stored.code_challenge,
            "redirect_uri": stored.redirect_uri,
            "agent_id": stored.agent_id,
            "redirect_uri_provided_explicitly": stored.redirect_uri_provided_explicitly,
            "expires_at": _iso_at(stored.expires_at),
        },
    )
    return rows[0] if rows else {}


async def _oauth_get_auth_code_row(code: str) -> Any | None:
    from patchcord.server.oauth import _StoredAuthCode

    rows = await _get_rows(
        OAUTH_AUTH_CODES_BASE,
        {"code": f"eq.{code}", "limit": "1"},
    )
    if not rows:
        return None
    row = rows[0]
    expires_at = parse_ts(row.get("expires_at"))
    if expires_at is None or expires_at.timestamp() <= time.time():
        await _oauth_delete_auth_code_row(code)
        return None
    return _StoredAuthCode(
        code=clean(str(row.get("code", ""))),
        client_id=clean(str(row.get("client_id", ""))),
        namespace_id=clean(str(row.get("namespace_id", ""))) or _OAUTH_DEFAULT_NAMESPACE,
        code_challenge=clean(str(row.get("code_challenge", ""))),
        redirect_uri=clean(str(row.get("redirect_uri", ""))),
        agent_id=clean(str(row.get("agent_id", ""))),
        redirect_uri_provided_explicitly=bool(row.get("redirect_uri_provided_explicitly", True)),
        expires_at=expires_at.timestamp(),
    )


async def _oauth_delete_auth_code_row(code: str) -> None:
    await _delete_rows(
        OAUTH_AUTH_CODES_BASE,
        {"code": f"eq.{code}"},
    )


async def _oauth_insert_access_token_row(data: dict[str, Any]) -> dict[str, Any]:
    rows = await _post_rows(OAUTH_ACCESS_TOKENS_BASE, data)
    return rows[0] if rows else {}


async def _oauth_get_access_token_row(access_token: str) -> dict[str, Any] | None:
    rows = await _get_rows(
        OAUTH_ACCESS_TOKENS_BASE,
        {"access_token": f"eq.{access_token}", "limit": "1"},
    )
    if not rows:
        return None
    row = rows[0]
    expires_at = parse_ts(row.get("expires_at"))
    if expires_at is None or expires_at.timestamp() <= time.time():
        await _oauth_delete_access_token_row(access_token)
        return None
    return row


async def _oauth_delete_access_token_row(access_token: str) -> None:
    await _delete_rows(
        OAUTH_ACCESS_TOKENS_BASE,
        {"access_token": f"eq.{access_token}"},
    )


async def _oauth_insert_refresh_token_row(data: dict[str, Any]) -> dict[str, Any]:
    rows = await _post_rows(OAUTH_REFRESH_TOKENS_BASE, data)
    return rows[0] if rows else {}


async def _oauth_get_refresh_token_row(refresh_token: str) -> Any | None:
    from patchcord.server.oauth import _StoredRefreshToken

    rows = await _get_rows(
        OAUTH_REFRESH_TOKENS_BASE,
        {"refresh_token": f"eq.{refresh_token}", "limit": "1"},
    )
    if not rows:
        return None
    row = rows[0]
    expires_at = parse_ts(row.get("expires_at"))
    if expires_at is None or expires_at.timestamp() <= time.time():
        await _oauth_delete_refresh_token_row(refresh_token)
        return None
    return _StoredRefreshToken(
        token=clean(str(row.get("refresh_token", ""))),
        client_id=clean(str(row.get("client_id", ""))),
        namespace_id=clean(str(row.get("namespace_id", ""))) or _OAUTH_DEFAULT_NAMESPACE,
        agent_id=clean(str(row.get("agent_id", ""))),
        scope=clean(str(row.get("scope", ""))) or "patchcord",
        expires_at=expires_at.timestamp(),
    )


async def _oauth_delete_refresh_token_row(refresh_token: str) -> None:
    await _delete_rows(
        OAUTH_REFRESH_TOKENS_BASE,
        {"refresh_token": f"eq.{refresh_token}"},
    )


# ---------------------------------------------------------------------------
# Server-specific context helpers
# ---------------------------------------------------------------------------


def _get_current_identity(ctx: Context) -> tuple[str, str]:
    """Return (namespace_id, agent_id) from the authenticated token.

    Both values are normalized to lowercase.
    """
    raw = None
    access = get_access_token()
    if access and access.client_id:
        raw = access.client_id
    elif ctx.client_id:
        raw = ctx.client_id
    if not raw:
        raise RuntimeError("Unauthorized request")
    if ":" in raw:
        ns, agent = raw.split(":", 1)
        return ns.lower(), agent.lower()
    return ("default", raw.lower())


def _is_oauth_agent(ctx: Context) -> bool:
    """Check if current request is from an OAuth-authenticated agent (not bearer token).

    Both bearer and OAuth agents go through load_access_token() and get an
    AccessToken object.  The difference: bearer tokens are in _bearer_token_cache
    (loaded from DB); OAuth tokens are not.
    """
    access = get_access_token()
    if not access or not access.client_id:
        return False
    token_hash = _hash_bearer_token(access.token)
    return token_hash not in _bearer_token_cache


async def _resolve_target_agent(
    sender_namespace: str,
    to_agent_raw: str,
    is_oauth: bool,
) -> tuple[str, str]:
    """Resolve to_agent within the sender's user scope.

    Handles agent@namespace syntax for cross-namespace targeting within the
    same user's namespaces. OAuth agents can also do bare-name search across
    all of the user's namespaces.
    Returns (target_namespace_id, target_agent_id).
    """
    # Normalize to lowercase
    to_agent_raw = to_agent_raw.strip().lower()

    # Get all namespaces owned by this user
    user_ns = await get_user_namespace_ids(sender_namespace)

    # Parse agent@namespace syntax — allowed for any agent type within user's namespaces
    if "@" in to_agent_raw:
        agent_id, target_ns = to_agent_raw.rsplit("@", 1)
        agent_id = agent_id.strip()
        target_ns = target_ns.strip()
        if not agent_id or not target_ns:
            raise ValueError("Invalid agent@namespace format")
        if not namespace_ids_match(target_ns, user_ns):
            raise ValueError(f"Namespace {target_ns!r} not found")
        return target_ns, agent_id

    # Check sender's own namespace first
    if not is_registry_disabled():
        try:
            rows = await _get_registry(
                {
                    "namespace_id": f"eq.{sender_namespace}",
                    "agent_id": f"eq.{to_agent_raw}",
                    "limit": "1",
                }
            )
            if rows:
                return sender_namespace, to_agent_raw
        except Exception:
            _log.debug("registry lookup failed for agent resolution", exc_info=True)

    # Search across all of the user's namespaces (OAuth or bearer with multi-ns user)
    if len(user_ns) > 1 and not is_registry_disabled():
        try:
            rows = await _get_registry(
                {
                    "namespace_id": user_ns_filter(user_ns),
                    "agent_id": f"eq.{to_agent_raw}",
                    "order": "last_seen.desc",
                    "limit": "10",
                }
            )
            if rows:
                namespaces = list(dict.fromkeys(row.get("namespace_id", "default") for row in rows))
                if len(namespaces) == 1:
                    return namespaces[0], to_agent_raw
                options = ", ".join(f"{to_agent_raw}@{ns}" for ns in namespaces)
                raise ValueError(f"Agent '{to_agent_raw}' exists in multiple namespaces. Disambiguate with: {options}")
        except ValueError:
            raise
        except Exception:
            _log.debug("user-scoped namespace registry lookup failed", exc_info=True)

    # Default: same namespace as sender
    return sender_namespace, to_agent_raw


def _request_header(ctx: Context, name: str) -> str:
    req = ctx.request_context.request
    if isinstance(req, Request):
        return req.headers.get(name, "")
    return ""


def _is_ip_address(value: str) -> bool:
    """Check if value looks like an IP (v4 or v6) rather than a hostname."""
    if ":" in value:
        return True
    import re

    return bool(re.match(r"^\d+\.\d+\.\d+\.\d+$", value))


def _derive_machine_name_from_request(request: Request, fallback_agent_id: str) -> str | None:
    """Derive machine name from request headers. Returns None if only an IP is available."""
    explicit = request.headers.get("x-patchcord-machine", "") or request.headers.get("x-machine-name", "")
    if explicit:
        return explicit[:120]
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
    if forwarded:
        ip = forwarded.split(",")[0].strip()[:120]
        return None if _is_ip_address(ip) else ip
    if request.client and request.client.host:
        ip = str(request.client.host)[:120]
        return None if _is_ip_address(ip) else ip
    return None


def _derive_machine_name(ctx: Context, fallback_agent_id: str) -> str | None:
    """Derive machine name. Returns None if only an IP is available (preserves existing registry value)."""
    explicit = _request_header(ctx, "x-patchcord-machine") or _request_header(ctx, "x-machine-name")
    if explicit:
        return explicit[:120]

    req = ctx.request_context.request
    if isinstance(req, Request):
        return _derive_machine_name_from_request(req, fallback_agent_id)
    return None


def _derive_client_type(ctx: Context) -> str:
    explicit = _request_header(ctx, "x-patchcord-client-type") or _request_header(ctx, "x-client-type")
    if explicit:
        return explicit[:80].strip().lower()

    req = ctx.request_context.request
    if isinstance(req, Request):
        ua = (req.headers.get("user-agent", "") or "").lower()
        # Order matters — more specific matches first
        if "openai-mcp" in ua or "chatgpt" in ua:
            return "chatgpt"
        if "codex" in ua:
            return "codex"
        if "cursor" in ua:
            return "cursor"
        if "windsurf" in ua:
            return "windsurf"
        if "gemini" in ua:
            return "gemini"
        if "claude-code" in ua:
            return "claude_code"
        if "claude-user" in ua or "claude.ai" in ua:
            return "claude_web"
        if "claude" in ua:
            return "claude_code"
    return "unknown"


def _derive_platform(ctx: Context) -> str:
    explicit = _request_header(ctx, "x-patchcord-platform") or _request_header(ctx, "x-platform")
    if explicit:
        return explicit[:80].strip().lower()
    return "unknown"


def _agent_display_name(agent_id: str, ctx: Context) -> str:
    explicit = _request_header(ctx, "x-patchcord-display-name")
    if explicit:
        return explicit[:120]
    return agent_id


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------


async def _touch_presence(namespace_id: str, agent_id_val: str, ctx: Context, force: bool = False) -> None:
    if is_registry_disabled():
        return

    presence_key = f"{namespace_id}:{agent_id_val}"
    now = time.time()
    if not force:
        last = _last_presence_write.get(presence_key, 0.0)
        if now - last < PRESENCE_WRITE_INTERVAL_SECONDS:
            return

    machine_name = _derive_machine_name(ctx, agent_id_val)
    display_name = _agent_display_name(agent_id_val, ctx)
    req = ctx.request_context.request
    client_type = _derive_client_type(ctx)
    platform = _derive_platform(ctx)
    meta_dict: dict[str, Any] = {
        "path": PATCHCORD_MCP_PATH,
        "client_type": client_type,
        "platform": platform,
    }
    if isinstance(req, Request):
        user_agent = req.headers.get("user-agent", "")
        if user_agent:
            meta_dict["user_agent"] = user_agent[:200]
        if req.url and req.url.hostname:
            meta_dict["request_host"] = req.url.hostname

    payload: dict[str, Any] = {
        "namespace_id": namespace_id,
        "agent_id": agent_id_val,
        "display_name": display_name,
        "status": "online",
        "last_seen": now_iso(),
        "updated_at": now_iso(),
        "meta": meta_dict,
    }
    # Only update machine_name if we have a real hostname, not an IP fallback.
    # This preserves the hostname set by the statusline plugin's x-patchcord-machine header.
    if machine_name is not None:
        payload["machine_name"] = machine_name

    try:
        await _upsert_registry(payload)
        _last_presence_write[presence_key] = now
    except Exception as exc:
        if is_missing_registry_table_error(exc):
            _disable_registry()


# ---------------------------------------------------------------------------
# Cleanup logic
# ---------------------------------------------------------------------------


async def _cleanup_count(table: str, params: dict[str, str]) -> int:
    """Count rows matching params via Supabase REST."""
    count_headers = {**HEADERS, "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"}
    resp = await http_client.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=count_headers, params=params)
    resp.raise_for_status()
    content_range = resp.headers.get("content-range", "")
    if "/" in content_range:
        count_str = content_range.split("/")[-1]
        if count_str != "*":
            return int(count_str)
    return 0


async def _cleanup_delete(table: str, params: dict[str, str]) -> int:
    """Count then delete rows matching params."""
    total = await _cleanup_count(table, params)
    if total == 0:
        return 0
    delete_headers = {**HEADERS, "Prefer": "return=minimal"}
    resp = await http_client.delete(f"{SUPABASE_URL}/rest/v1/{table}", headers=delete_headers, params=params)
    resp.raise_for_status()
    return total


async def _run_cleanup(max_age_days: int = 7, dry_run: bool = False) -> dict[str, Any]:
    """Clean old messages, stale registry, old attachments. No OAuth — that's manual."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    results: dict[str, Any] = {}

    # 1. Messages (replies first due to FK)
    try:
        if dry_run:
            count = await _cleanup_count("agent_messages", {"created_at": f"lt.{cutoff}"})
        else:
            await _cleanup_delete("agent_messages", {"created_at": f"lt.{cutoff}", "reply_to": "not.is.null"})
            count = await _cleanup_delete("agent_messages", {"created_at": f"lt.{cutoff}"})
        results["messages"] = count
    except Exception as exc:
        results["messages"] = f"error: {exc}"

    # 2. Stale registry -> offline
    try:
        stale_params = {"last_seen": f"lt.{cutoff}", "status": "eq.online"}
        count = await _cleanup_count("agent_registry", stale_params)
        if count > 0 and not dry_run:
            resp = await http_client.patch(
                f"{SUPABASE_URL}/rest/v1/agent_registry",
                headers={**HEADERS, "Prefer": "return=minimal"},
                params=stale_params,
                json={"status": "offline"},
            )
            resp.raise_for_status()
        results["registry_marked_offline"] = count
    except Exception as exc:
        results["registry_marked_offline"] = f"error: {exc}"

    # 3. Attachments
    try:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        old_paths: list[str] = []
        resp = await _storage_request(
            "POST", f"/object/list/{ATTACHMENT_BUCKET}", json_body={"prefix": "", "limit": 1000, "offset": 0}
        )
        namespaces = resp.json() if isinstance(resp.json(), list) else []
        for ns in namespaces:
            ns_name = ns.get("name", "")
            if not ns_name:
                continue
            resp2 = await _storage_request(
                "POST", f"/object/list/{ATTACHMENT_BUCKET}", json_body={"prefix": ns_name, "limit": 1000, "offset": 0}
            )
            objects = resp2.json() if isinstance(resp2.json(), list) else []
            for obj in objects:
                created = obj.get("created_at") or obj.get("updated_at", "")
                if not created:
                    continue
                try:
                    obj_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if obj_dt < cutoff_dt:
                    obj_name = obj.get("name", "")
                    if obj_name:
                        old_paths.append(f"{ns_name}/{obj_name}")
        if old_paths and not dry_run:
            for i in range(0, len(old_paths), 100):
                batch = old_paths[i : i + 100]
                try:
                    await _storage_request("DELETE", f"/object/{ATTACHMENT_BUCKET}", json_body={"prefixes": batch})
                except Exception:
                    _log.debug("attachment batch delete failed", exc_info=True)
        results["attachments"] = len(old_paths)
    except Exception:
        results["attachments"] = 0

    return results


async def _run_oauth_cleanup(dry_run: bool = False) -> dict[str, Any]:
    """Clean expired OAuth tokens and auth codes. Manual-only, never automatic."""
    now = datetime.now(timezone.utc).isoformat()
    results: dict[str, Any] = {}
    for table in ("oauth_auth_codes", "oauth_access_tokens", "oauth_refresh_tokens"):
        try:
            if dry_run:
                count = await _cleanup_count(table, {"expires_at": f"lt.{now}"})
            else:
                count = await _cleanup_delete(table, {"expires_at": f"lt.{now}"})
            results[table] = count
        except Exception:
            results[table] = 0
    return results


async def _periodic_cleanup() -> None:
    """Background task that runs cleanup on a schedule."""
    from patchcord.server.config import CLEANUP_INTERVAL_HOURS

    interval = CLEANUP_INTERVAL_HOURS * 3600
    # Wait a bit after startup before first run
    await asyncio.sleep(60)
    while True:
        try:
            results = await _run_cleanup(max_age_days=CLEANUP_MAX_AGE_DAYS)
            total = sum(v for v in results.values() if isinstance(v, int))
            if total > 0:
                print(f"[cleanup] Removed {total} items (max_age={CLEANUP_MAX_AGE_DAYS}d): {results}")
        except Exception as exc:
            print(f"[cleanup] Error: {exc}")
        await asyncio.sleep(interval)
