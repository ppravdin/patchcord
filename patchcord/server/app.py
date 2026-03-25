"""FastMCP server instance, custom routes, middleware, and main() entrypoint."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from patchcord.core import MCP_INSTRUCTIONS, STATUS_DEFERRED, STATUS_PENDING, STATUS_READ, http_error
from patchcord.server.config import (
    ANON_RATE_LIMIT_PER_MINUTE,
    CLEANUP_MAX_AGE_DAYS,
    OAUTH_CLIENT_MAP,
    PATCHCORD_BEARER_PATH,
    PATCHCORD_HOST,
    PATCHCORD_MCP_PATH,
    PATCHCORD_NAME,
    PATCHCORD_PORT,
    PATCHCORD_PUBLIC_URL,
    PATCHCORD_STATELESS_HTTP,
    RATE_BAN_SECONDS,
    RATE_LIMIT_PER_MINUTE,
    SUPABASE_URL,
    _parse_ns_agent,
)
from patchcord.server.helpers import (
    _delete_rows,
    _derive_machine_name_from_request,
    _get_messages,
    _get_rows,
    _patch_message,
    _periodic_cleanup,
    _post_message,
    _post_rows,
    _resolve_target_agent,
    _run_cleanup,
    _run_oauth_cleanup,
    _upsert_registry,
    lookup_bearer_token,
)
from patchcord.server.oauth import PatchcordOAuthProvider

# ---------------------------------------------------------------------------
# OAuth provider + MCP server instance
# ---------------------------------------------------------------------------

_oauth_provider = PatchcordOAuthProvider(
    oauth_client_to_identity=OAUTH_CLIENT_MAP,
)

mcp = FastMCP(
    PATCHCORD_NAME,
    instructions=MCP_INSTRUCTIONS,
    host=PATCHCORD_HOST,
    port=PATCHCORD_PORT,
    streamable_http_path=PATCHCORD_MCP_PATH,
    stateless_http=PATCHCORD_STATELESS_HTTP,
    auth=AuthSettings(
        issuer_url=PATCHCORD_PUBLIC_URL,
        resource_server_url=PATCHCORD_PUBLIC_URL,
        required_scopes=[],
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["patchcord"],
        ),
    ),
    auth_server_provider=_oauth_provider,
)

# Register all @mcp.tool handlers
from patchcord.server.tools import register as _register_tools  # noqa: E402

_register_tools(mcp)


# ---------------------------------------------------------------------------
# Custom HTTP routes
# ---------------------------------------------------------------------------


@mcp.custom_route("/api/inbox", methods=["GET"], include_in_schema=False)
async def api_inbox(request: Request) -> Response:
    """Lightweight REST endpoint for hook scripts to check pending message count."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return JSONResponse({"error": "missing bearer token"}, status_code=401)
    token = auth_header[7:].strip()
    if not token:
        return JSONResponse({"error": "empty bearer token"}, status_code=401)

    access = await _oauth_provider.load_access_token(token)
    if not access or not access.client_id:
        return JSONResponse({"error": "invalid token"}, status_code=401)

    namespace_id, agent_id_val = _parse_ns_agent(access.client_id)

    status_filter = request.query_params.get("status", STATUS_PENDING)
    if status_filter not in (STATUS_PENDING, STATUS_READ, STATUS_DEFERRED):
        status_filter = STATUS_PENDING
    try:
        limit = int(request.query_params.get("limit", "5"))
    except ValueError:
        limit = 5
    limit = max(1, min(limit, 100))

    try:
        rows = await _get_messages(
            {
                "namespace_id": f"eq.{namespace_id}",
                "to_agent": f"eq.{agent_id_val}",
                "status": f"eq.{status_filter}",
                "order": "created_at.desc",
                "limit": str(limit),
                "select": "id,from_agent,content,created_at",
            }
        )
    except Exception as exc:
        return JSONResponse({"error": http_error(exc)}, status_code=502)

    messages = [
        {
            "message_id": row.get("id"),
            "from": row.get("from_agent"),
            "preview": (row.get("content") or "")[:100],
            "sent_at": row.get("created_at"),
        }
        for row in rows
    ]
    # Update presence; only overwrite machine_name if caller sends a real hostname
    machine_name = _derive_machine_name_from_request(request, agent_id_val)
    reg_payload: dict[str, str] = {
        "namespace_id": namespace_id,
        "agent_id": agent_id_val,
        "status": "online",
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if machine_name is not None:
        reg_payload["machine_name"] = machine_name
    try:
        await _upsert_registry(reg_payload)
    except Exception:
        _logger.debug("registry upsert failed", exc_info=True)

    return JSONResponse(
        {
            "pending_count": len(rows),
            "messages": messages,
            "agent_id": agent_id_val,
            "namespace_id": namespace_id,
            "machine_name": machine_name,
        }
    )


# ---------------------------------------------------------------------------
# Channel endpoints (push delivery for Claude Code channel plugin)
# ---------------------------------------------------------------------------


async def _channel_auth(request: Request) -> tuple[str, str] | Response:
    """Authenticate a channel request via bearer token. Returns (namespace_id, agent_id) or error Response."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return JSONResponse({"error": "missing bearer token"}, status_code=401)
    token = auth_header[7:].strip()
    if not token:
        return JSONResponse({"error": "empty bearer token"}, status_code=401)
    access = await _oauth_provider.load_access_token(token)
    if not access or not access.client_id:
        return JSONResponse({"error": "invalid token"}, status_code=401)
    return _parse_ns_agent(access.client_id)


@mcp.custom_route("/api/channel/poll", methods=["POST"], include_in_schema=False)
async def api_channel_poll(request: Request) -> Response:
    """Poll for pending messages. Used by channel plugin for push delivery."""
    result = await _channel_auth(request)
    if isinstance(result, Response):
        return result
    namespace_id, agent_id = result

    try:
        rows = await _get_messages({
            "namespace_id": f"eq.{namespace_id}",
            "to_agent": f"eq.{agent_id}",
            "status": f"eq.{STATUS_PENDING}",
            "order": "created_at.asc",
            "limit": "50",
            "select": "id,from_agent,content,created_at,namespace_id,reply_to,encrypted",
        })
    except Exception as exc:
        return JSONResponse({"error": http_error(exc)}, status_code=500)

    # Mark as read (delivered via channel)
    for row in rows:
        try:
            await _patch_message(row["id"], {"status": STATUS_READ})
        except Exception:
            pass

    messages = [
        {
            "id": row.get("id"),
            "from_agent": row.get("from_agent"),
            "content": row.get("content"),
            "created_at": row.get("created_at"),
            "namespace_id": row.get("namespace_id"),
            "reply_to": row.get("reply_to"),
        }
        for row in rows
    ]
    return JSONResponse(messages)


@mcp.custom_route("/api/channel/send", methods=["POST"], include_in_schema=False)
async def api_channel_send(request: Request) -> Response:
    """Send a message. Used by channel plugin's send_message tool."""
    result = await _channel_auth(request)
    if isinstance(result, Response):
        return result
    namespace_id, agent_id = result

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    to_agent = (body.get("to_agent") or "").strip()
    content = (body.get("content") or "").strip()
    if not to_agent:
        return JSONResponse({"error": "to_agent required"}, status_code=400)
    if not content:
        return JSONResponse({"error": "content required"}, status_code=400)
    if len(content) > 50000:
        return JSONResponse({"error": "content exceeds 50000 characters"}, status_code=400)

    try:
        target_ns, to_agent_resolved = await _resolve_target_agent(
            namespace_id, to_agent, False, sender_agent_id=agent_id
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)

    try:
        msg = await _post_message({
            "namespace_id": target_ns,
            "from_agent": agent_id,
            "to_agent": to_agent_resolved,
            "content": content,
            "status": STATUS_PENDING,
        })
    except Exception as exc:
        return JSONResponse({"error": http_error(exc)}, status_code=500)

    return JSONResponse({"id": msg.get("id"), "status": "sent", "to_agent": to_agent_resolved})


@mcp.custom_route("/api/channel/reply", methods=["POST"], include_in_schema=False)
async def api_channel_reply(request: Request) -> Response:
    """Reply to a message. Used by channel plugin's reply tool."""
    result = await _channel_auth(request)
    if isinstance(result, Response):
        return result
    namespace_id, agent_id = result

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    message_id = (body.get("message_id") or "").strip()
    content = (body.get("content") or "").strip()
    if not message_id:
        return JSONResponse({"error": "message_id required"}, status_code=400)
    if not content:
        return JSONResponse({"error": "content required"}, status_code=400)
    if len(content) > 50000:
        return JSONResponse({"error": "content exceeds 50000 characters"}, status_code=400)

    # Load original message
    try:
        originals = await _get_messages({"id": f"eq.{message_id}", "limit": "1", "select": "id,from_agent,to_agent,namespace_id,status"})
    except Exception as exc:
        return JSONResponse({"error": http_error(exc)}, status_code=500)
    if not originals:
        return JSONResponse({"error": "message not found"}, status_code=404)

    original = originals[0]
    if original.get("to_agent") != agent_id:
        return JSONResponse({"error": "cannot reply to a message not addressed to you"}, status_code=403)

    orig_ns = original.get("namespace_id", namespace_id)

    try:
        await _patch_message(message_id, {"status": "replied"})
        msg = await _post_message({
            "namespace_id": orig_ns,
            "from_agent": agent_id,
            "to_agent": original["from_agent"],
            "content": content,
            "reply_to": message_id,
            "status": STATUS_PENDING,
        })
    except Exception as exc:
        return JSONResponse({"error": http_error(exc)}, status_code=500)

    return JSONResponse({"id": msg.get("id"), "status": "replied", "to_agent": original["from_agent"]})


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health(_: Request) -> Response:
    return JSONResponse({"status": "ok", "service": PATCHCORD_NAME})


@mcp.custom_route("/.well-known/openai-apps-challenge", methods=["GET"], include_in_schema=False)
async def openai_verification(_: Request) -> Response:
    token = os.getenv("OPENAI_VERIFICATION_TOKEN", "not-configured")
    return PlainTextResponse(token, media_type="text/plain")


_CLEANUP_TOKEN = os.environ.get("PATCHCORD_CLEANUP_TOKEN", "").strip()


async def _is_valid_bearer(token: str) -> bool:
    """Check if a bearer token is valid (DB lookup)."""
    return (await lookup_bearer_token(token)) is not None


def _is_cleanup_authorized(token: str) -> bool:
    """Cleanup requires a dedicated PATCHCORD_CLEANUP_TOKEN, not any agent token."""
    return bool(_CLEANUP_TOKEN) and token == _CLEANUP_TOKEN


@mcp.custom_route("/api/cleanup", methods=["POST"], include_in_schema=False)
async def api_cleanup(request: Request) -> Response:
    """Cleanup old messages and attachments. Requires dedicated cleanup token."""
    auth = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if not auth or not _is_cleanup_authorized(auth):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    dry_run = request.query_params.get("dry_run", "").lower() in ("1", "true", "yes")
    max_age = CLEANUP_MAX_AGE_DAYS
    try:
        if "max_age_days" in request.query_params:
            max_age = max(1, min(365, int(request.query_params["max_age_days"])))
    except ValueError:
        pass
    results = await _run_cleanup(max_age_days=max_age, dry_run=dry_run)
    return JSONResponse({"status": "ok", "dry_run": dry_run, "max_age_days": max_age, **results})


@mcp.custom_route("/api/cleanup/oauth", methods=["POST"], include_in_schema=False)
async def api_cleanup_oauth(request: Request) -> Response:
    """Cleanup expired OAuth tokens and auth codes. Requires dedicated cleanup token."""
    auth = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if not auth or not _is_cleanup_authorized(auth):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    dry_run = request.query_params.get("dry_run", "").lower() in ("1", "true", "yes")
    results = await _run_oauth_cleanup(dry_run=dry_run)
    return JSONResponse({"status": "ok", "dry_run": dry_run, **results})


@mcp.custom_route("/.well-known/security.txt", methods=["GET"], include_in_schema=False)
async def security_txt(_: Request) -> Response:
    return PlainTextResponse(
        "Contact: https://github.com/ppravdin/patchcord/security/advisories/new\nPreferred-Languages: en\n",
        media_type="text/plain",
    )


@mcp.custom_route("/.well-known/openid-configuration", methods=["GET"], include_in_schema=False)
async def openid_configuration(_: Request) -> Response:
    """OIDC discovery shim — points clients to the OAuth 2.0 authorization server metadata."""
    base = PATCHCORD_PUBLIC_URL.rstrip("/")
    return JSONResponse(
        {
            "issuer": f"{base}/",
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "scopes_supported": ["patchcord"],
        }
    )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

_logger = logging.getLogger("patchcord.ratelimit")


_BANS_TABLE = "rate_limit_bans"
_BANS_BASE = f"{SUPABASE_URL}/rest/v1/{_BANS_TABLE}"


def _hash_token(token: str) -> str:
    """SHA-256 hash of a bearer token (never store raw tokens)."""
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimitMiddleware:
    """Per-token rate limiter with persistent bans.

    Sliding window counters are kept in-memory (fast, per-request).
    Bans are written through to Supabase so they survive server restarts.
    On first request, active bans are loaded from the DB into memory.
    If the DB write fails, the in-memory ban still works (graceful degradation).
    """

    def __init__(self, app):
        self.app = app
        # token -> (count, window_start_time)  [monotonic clock]
        self._counters: dict[str, tuple[int, float]] = {}
        # token_hash -> ban_expires_at  [wall-clock UTC timestamp]
        self._bans: dict[str, float] = {}
        self._bans_loaded = False
        self._bans_load_lock = asyncio.Lock()
        # Whether the DB table exists (disable writes if missing)
        self._db_disabled = False

    async def _load_bans_from_db(self) -> None:
        """Load all non-expired bans from Supabase into memory. Called once on first request."""
        if self._bans_loaded:
            return
        async with self._bans_load_lock:
            if self._bans_loaded:
                return
            try:
                now_iso = datetime.now(timezone.utc).isoformat()
                rows = await _get_rows(
                    _BANS_BASE,
                    {"banned_until": f"gt.{now_iso}", "select": "token_hash,banned_until"},
                )
                for row in rows:
                    token_hash = row.get("token_hash", "")
                    banned_until_str = row.get("banned_until", "")
                    if not token_hash or not banned_until_str:
                        continue
                    try:
                        banned_until_dt = datetime.fromisoformat(banned_until_str.replace("Z", "+00:00"))
                        self._bans[token_hash] = banned_until_dt.timestamp()
                    except (ValueError, TypeError):
                        continue
                loaded = len(self._bans)
                if loaded > 0:
                    _logger.info("Loaded %d active ban(s) from database", loaded)
            except Exception as exc:
                body = str(exc).lower()
                if "does not exist" in body or "not found" in body or "relation" in body or "schema cache" in body:
                    _logger.warning("rate_limit_bans table not found — DB persistence disabled")
                    self._db_disabled = True
                else:
                    _logger.warning("Failed to load bans from DB (will retry next restart): %s", exc)
            finally:
                self._bans_loaded = True

    async def _persist_ban(self, token_hash: str, banned_until: float) -> None:
        """Write a ban to Supabase (fire-and-forget, best-effort)."""
        if self._db_disabled:
            return
        try:
            banned_until_iso = datetime.fromtimestamp(banned_until, tz=timezone.utc).isoformat()
            await _post_rows(
                _BANS_BASE,
                {"token_hash": token_hash, "banned_until": banned_until_iso},
                params={"on_conflict": "token_hash"},
                prefer="resolution=merge-duplicates,return=representation",
            )
        except Exception as exc:
            _logger.warning("Failed to persist ban to DB (in-memory ban still active): %s", exc)

    async def _delete_ban_from_db(self, token_hash: str) -> None:
        """Remove an expired ban from Supabase (best-effort)."""
        if self._db_disabled:
            return
        try:
            await _delete_rows(_BANS_BASE, {"token_hash": f"eq.{token_hash}"})
        except Exception:
            _logger.debug("ban cleanup failed", exc_info=True)

    def _extract_token(self, scope) -> str | None:
        for key, value in scope.get("headers", []):
            if key == b"authorization":
                decoded = value.decode("latin-1", errors="replace")
                if decoded.lower().startswith("bearer "):
                    return decoded[7:].strip()
                break
        return None

    def _redact_token(self, token: str) -> str:
        if len(token) <= 8:
            return "***"
        return token[:4] + "..." + token[-4:]

    def _get_peer_ip(self, scope) -> str:
        """Socket-level peer address — unspoofable, used for rate limiting."""
        client = scope.get("client")
        if client:
            return client[0]
        return "unknown"

    def _get_client_ip(self, scope) -> str:
        """Best-effort client IP for logging (may use X-Forwarded-For)."""
        for key, value in scope.get("headers", []):
            if key == b"x-forwarded-for":
                return value.decode("latin-1", errors="replace").split(",")[0].strip()
        return self._get_peer_ip(scope)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        token = self._extract_token(scope)
        if not token:
            # Rate limit unauthenticated requests by peer IP (lighter limit, no DB bans).
            # Uses socket-level peer address, not X-Forwarded-For, to prevent spoofing.
            client_ip = self._get_peer_ip(scope)
            rate_key = f"anon:{client_ip}"
            mono_now = time.monotonic()
            count, window_start = self._counters.get(rate_key, (0, mono_now))
            if mono_now - window_start >= 60:
                count = 1
                window_start = mono_now
            else:
                count += 1
            self._counters[rate_key] = (count, window_start)

            if count > ANON_RATE_LIMIT_PER_MINUTE:
                _logger.warning(
                    "Anon rate limit exceeded: ip=%s count=%d",
                    client_ip,
                    count,
                )
                response = JSONResponse(
                    {"error": "rate_limited", "retry_after": 60},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
                await response(scope, receive, send)
                return

            await self.app(scope, receive, send)
            return

        # Ensure bans are loaded from DB on first request
        if not self._bans_loaded:
            await self._load_bans_from_db()

        now = time.time()  # wall-clock UTC for DB compatibility
        token_hash = _hash_token(token)

        # Check ban (keyed by hash)
        ban_expires = self._bans.get(token_hash)
        if ban_expires is not None:
            if now < ban_expires:
                remaining = int(ban_expires - now)
                response = JSONResponse(
                    {"error": "rate_limited", "retry_after": remaining},
                    status_code=429,
                    headers={"Retry-After": str(remaining)},
                )
                await response(scope, receive, send)
                return
            else:
                # Ban expired — lazy cleanup from memory and DB
                del self._bans[token_hash]
                asyncio.ensure_future(self._delete_ban_from_db(token_hash))

        # Sliding window counter (uses monotonic clock — not persisted)
        # Evict stale entries to prevent memory growth from random tokens
        if len(self._counters) > 10000:
            cutoff = time.monotonic() - 120
            self._counters = {k: v for k, v in self._counters.items() if v[1] > cutoff}
        mono_now = time.monotonic()
        count, window_start = self._counters.get(token, (0, mono_now))
        if mono_now - window_start >= 60:
            # New window
            count = 1
            window_start = mono_now
        else:
            count += 1
        self._counters[token] = (count, window_start)

        if count > RATE_LIMIT_PER_MINUTE:
            # Ban the token — write to memory and DB
            ban_until = now + RATE_BAN_SECONDS
            self._bans[token_hash] = ban_until
            client_ip = self._get_client_ip(scope)
            _logger.warning(
                "Rate limit exceeded: token=%s ip=%s count=%d — banned for %ds",
                self._redact_token(token),
                client_ip,
                count,
                RATE_BAN_SECONDS,
            )
            # Persist ban to DB (non-blocking, best-effort)
            asyncio.ensure_future(self._persist_ban(token_hash, ban_until))
            response = JSONResponse(
                {"error": "rate_limited", "retry_after": RATE_BAN_SECONDS},
                status_code=429,
                headers={"Retry-After": str(RATE_BAN_SECONDS)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------

_cleanup_task: asyncio.Task[None] | None = None

# Graceful shutdown: when SIGTERM arrives (docker stop), set this flag so
# all wait_for_message loops exit immediately with a clean response.
# Clients retry on the new container.
_shutting_down = False


def is_shutting_down() -> bool:
    return _shutting_down


def main() -> None:
    import uvicorn

    CSP_POLICY = f"default-src 'self'; connect-src 'self' {SUPABASE_URL}; frame-ancestors 'none'"

    class CSPMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return

            async def send_with_csp(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"content-security-policy", CSP_POLICY.encode()))
                    headers.append((b"referrer-policy", b"no-referrer"))
                    headers.append((b"x-frame-options", b"DENY"))
                    headers.append((b"x-content-type-options", b"nosniff"))
                    message["headers"] = headers
                await send(message)

            await self.app(scope, receive, send_with_csp)

    starlette_app = mcp.streamable_http_app()
    if hasattr(starlette_app, "router"):
        starlette_app.router.redirect_slashes = False

    class BearerPathMiddleware:
        """Rewrite PATCHCORD_BEARER_PATH -> PATCHCORD_MCP_PATH and suppress OAuth discovery for it.

        Clients connecting to /mcp/cursor get their path rewritten to /mcp so
        FastMCP handles it normally.  The path-specific .well-known endpoint
        returns empty metadata (no authorization_servers) so MCP clients that
        follow RFC 9728 will skip OAuth and use the bearer token from headers.
        """

        def __init__(self, app):
            self.app = app
            bp = PATCHCORD_BEARER_PATH.rstrip("/")
            self._bearer_prefix = bp
            # Path-specific .well-known for bearer endpoint (RFC 9728 S3.1)
            self._wellknown_resource = f"/.well-known/oauth-protected-resource{bp}"
            self._bearer_metadata = json.dumps(
                {
                    "resource": f"{PATCHCORD_PUBLIC_URL}{bp}",
                    "bearer_methods_supported": ["header"],
                }
            ).encode()

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return

            path = scope.get("path", "")

            # Serve bearer-only resource metadata for the bearer path
            if path == self._wellknown_resource:
                response = Response(
                    content=self._bearer_metadata,
                    status_code=200,
                    media_type="application/json",
                )
                await response(scope, receive, send)
                return

            # Rewrite bearer path to main MCP path
            if path == self._bearer_prefix or path.startswith(self._bearer_prefix + "/"):
                scope = dict(scope)
                scope["path"] = PATCHCORD_MCP_PATH + path[len(self._bearer_prefix) :]

            await self.app(scope, receive, send)

    starlette_app = RateLimitMiddleware(BearerPathMiddleware(CSPMiddleware(starlette_app)))

    async def serve() -> None:
        import signal

        global _cleanup_task, _shutting_down
        _cleanup_task = asyncio.create_task(_periodic_cleanup())

        def _handle_sigterm(*_args):
            global _shutting_down
            _shutting_down = True
            print("SIGTERM received — draining wait_for_message loops", file=__import__("sys").stderr)

        signal.signal(signal.SIGTERM, _handle_sigterm)

        config = uvicorn.Config(
            starlette_app,
            host=mcp.settings.host,
            port=mcp.settings.port,
            log_level=mcp.settings.log_level.lower(),
            proxy_headers=True,
            forwarded_allow_ips="*",
            timeout_graceful_shutdown=10,
        )
        server = uvicorn.Server(config)
        await server.serve()

    asyncio.run(serve())


if __name__ == "__main__":
    main()
