"""OAuth 2.0 provider: data classes, token management, Supabase-backed storage."""

from __future__ import annotations

import logging
import secrets
import sys
import time
from dataclasses import dataclass
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationParams,
    AuthorizeError,
    OAuthToken,
    RegistrationError,
)
from mcp.shared.auth import OAuthClientInformationFull

from patchcord.core import clean, now_iso, parse_ts
from patchcord.server.config import (
    _OAUTH_DEFAULT_NAMESPACE,
    OAUTH_ACCESS_TOKEN_TTL_SECONDS,
    OAUTH_REFRESH_TOKEN_TTL_SECONDS,
    _detect_agent_from_client_info,
    _iso_at,
    _scope_list,
    validate_client_uri_redirect_match,
    validate_known_client_redirect_uris,
)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OAuth data classes
# ---------------------------------------------------------------------------


@dataclass
class _StoredAuthCode:
    code: str
    client_id: str
    namespace_id: str
    code_challenge: str
    redirect_uri: str
    agent_id: str
    redirect_uri_provided_explicitly: bool = True
    expires_at: float = 0.0
    scope: str = "patchcord"

    def __post_init__(self) -> None:
        if self.expires_at == 0.0:
            self.expires_at = time.time() + 600

    @property
    def scopes(self) -> list[str]:
        return self.scope.split() if self.scope else []


@dataclass
class _IssuedAccessToken:
    client_id: str
    namespace_id: str
    agent_id: str
    scope: str
    expires_at: float


@dataclass
class _StoredRefreshToken:
    token: str
    client_id: str
    namespace_id: str
    agent_id: str
    scope: str
    expires_at: float

    @property
    def scopes(self) -> list[str]:
        return self.scope.split() if self.scope else []


# ---------------------------------------------------------------------------
# OAuth provider
# ---------------------------------------------------------------------------


class PatchcordOAuthProvider:
    """OAuth 2.0 provider that supports both static bearer tokens and full OAuth flow."""

    def __init__(
        self,
        oauth_client_to_identity: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self._oauth_client_to_identity = oauth_client_to_identity or {}
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._client_agent_map: dict[str, str] = {}
        self._client_namespace_map: dict[str, str] = {}
        self._auth_codes: dict[str, _StoredAuthCode] = {}
        self._issued_tokens: dict[str, _IssuedAccessToken] = {}
        self._refresh_tokens: dict[str, _StoredRefreshToken] = {}

    def _resolve_identity_for_client(self, client_id: str) -> tuple[str, str]:
        explicit = self._oauth_client_to_identity.get(client_id)
        if explicit is not None:
            return explicit
        namespace_id = self._client_namespace_map.get(client_id, _OAUTH_DEFAULT_NAMESPACE)
        agent_id_val = self._client_agent_map.get(client_id)
        if not agent_id_val:
            raise RuntimeError(f"No agent identity for OAuth client {client_id!r}. Client must register first.")
        return namespace_id, agent_id_val

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        from patchcord.server import helpers

        client_info = self._clients.get(client_id)
        if client_info is not None:
            return client_info

        if helpers.is_oauth_storage_disabled():
            return None
        try:
            row = await helpers._oauth_get_client_row(client_id)
        except Exception as exc:
            if helpers._is_missing_oauth_table_error(exc):
                helpers.disable_oauth_storage()
                return None
            raise
        if not row:
            return None

        payload = row.get("client_info")
        if not isinstance(payload, dict):
            return None

        client_info = OAuthClientInformationFull.model_validate(payload)
        self._clients[client_id] = client_info
        namespace_id = clean(str(row.get("namespace_id", ""))) or _OAUTH_DEFAULT_NAMESPACE
        self._client_namespace_map[client_id] = namespace_id
        agent_id_val = clean(str(row.get("agent_id", "")))
        if agent_id_val:
            self._client_agent_map[client_id] = agent_id_val
        return client_info

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        from patchcord.server import helpers

        if not client_info.scope or "patchcord" not in client_info.scope:
            client_info.scope = "patchcord"
        if not client_info.client_id:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description="OAuth client registration missing client_id",
            )
        # Idempotent re-registration: if client_id already exists, return without
        # modifying stored metadata. Prevents overwrite while allowing reconnects.
        existing = await self.get_client(client_info.client_id)
        if existing is not None:
            return
        explicit = self._oauth_client_to_identity.get(client_info.client_id)
        if explicit is not None:
            namespace_id, agent_id_val = explicit
        else:
            namespace_id = _OAUTH_DEFAULT_NAMESPACE
            agent_id_val, is_known = _detect_agent_from_client_info(client_info)
            if agent_id_val is None:
                raise RegistrationError(
                    error="invalid_client_metadata",
                    error_description=(
                        "Unknown OAuth client: could not detect agent identity from "
                        "registration metadata. Provide a recognized client_name, "
                        "client_uri, or redirect_uris, or ask the server admin to add "
                        "an explicit PATCHCORD_OAUTH_CLIENTS mapping."
                    ),
                )
            if is_known:
                # Validate redirect URIs match expected domains for known clients.
                # Prevents impersonation: can't claim to be "chatgpt" with redirect to evil.com.
                err = validate_known_client_redirect_uris(agent_id_val, client_info.redirect_uris)
                if err:
                    raise RegistrationError(error="invalid_redirect_uri", error_description=err)
                # Redirect URI validated against known domains — prevents impersonation.
                # PKCE protects the code exchange, so spoofed metadata can't steal tokens.
            else:
                # For unknown clients, verify redirect_uri domains match client_uri domain.
                err = validate_client_uri_redirect_match(client_info.client_uri, client_info.redirect_uris)
                if err:
                    raise RegistrationError(error="invalid_redirect_uri", error_description=err)
        self._clients[client_info.client_id] = client_info
        self._client_agent_map[client_info.client_id] = agent_id_val
        self._client_namespace_map[client_info.client_id] = namespace_id
        if not helpers.is_oauth_storage_disabled():
            try:
                await helpers._oauth_upsert_client_row(
                    {
                        "client_id": client_info.client_id,
                        "namespace_id": namespace_id,
                        "agent_id": agent_id_val,
                        "client_info": client_info.model_dump(mode="json"),
                        "updated_at": now_iso(),
                    }
                )
            except Exception as exc:
                if helpers._is_missing_oauth_table_error(exc):
                    helpers.disable_oauth_storage()
                else:
                    raise
        # Pre-populate agent_registry with client_type so dashboard shows
        # correct icons immediately, without waiting for a tool call.
        if not helpers.is_registry_disabled():
            meta: dict[str, Any] = {"client_type": agent_id_val}
            if client_info.client_name:
                meta["client_name"] = client_info.client_name
            try:
                await helpers._upsert_registry(
                    {
                        "namespace_id": namespace_id,
                        "agent_id": agent_id_val,
                        "status": "offline",
                        "last_seen": now_iso(),
                        "updated_at": now_iso(),
                        "meta": meta,
                    }
                )
            except Exception as exc:
                if helpers.is_missing_registry_table_error(exc):
                    helpers._disable_registry()
                else:
                    _log.debug("failed to pre-populate registry on OAuth register", exc_info=True)
        print(
            f"OAuth client registered: client_id={client_info.client_id} "
            f"client_name={client_info.client_name!r} -> identity={namespace_id}:{agent_id_val}",
            file=sys.stderr,
        )

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        from patchcord.server import helpers

        # Allow if the client has an identity — either from explicit env-var
        # mapping or from auto-detection during registration (which already
        # validated redirect URIs against known client domains + PKCE protects
        # the code exchange).
        if client.client_id not in self._oauth_client_to_identity and client.client_id not in self._client_agent_map:
            raise AuthorizeError(
                error="access_denied",
                error_description="Unknown client. Re-register via the OAuth flow.",
            )

        code = secrets.token_urlsafe(48)
        namespace_id, agent_id_val = self._resolve_identity_for_client(client.client_id)
        stored = _StoredAuthCode(
            code=code,
            client_id=client.client_id,
            namespace_id=namespace_id,
            code_challenge=params.code_challenge,
            redirect_uri=str(params.redirect_uri),
            agent_id=agent_id_val,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
        )
        self._auth_codes[code] = stored
        if not helpers.is_oauth_storage_disabled():
            try:
                await helpers._oauth_insert_auth_code_row(code, stored)
            except Exception as exc:
                if helpers._is_missing_oauth_table_error(exc):
                    helpers.disable_oauth_storage()
                else:
                    raise
        redirect = str(params.redirect_uri)
        sep = "&" if "?" in redirect else "?"
        url = f"{redirect}{sep}code={code}"
        if params.state:
            url += f"&state={params.state}"
        return url

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> _StoredAuthCode | None:
        from patchcord.server import helpers

        stored = self._auth_codes.get(authorization_code)
        if stored and stored.expires_at <= time.time():
            self._auth_codes.pop(authorization_code, None)
            stored = None

        if stored is None:
            if not helpers.is_oauth_storage_disabled():
                try:
                    stored = await helpers._oauth_get_auth_code_row(authorization_code)
                except Exception as exc:
                    if helpers._is_missing_oauth_table_error(exc):
                        helpers.disable_oauth_storage()
                        stored = None
                    else:
                        raise
                if stored is not None:
                    self._auth_codes[authorization_code] = stored

        if stored and stored.client_id == client.client_id:
            return stored
        return None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: _StoredAuthCode
    ) -> OAuthToken:
        from patchcord.server import helpers

        code = authorization_code.code or next(
            (k for k, v in self._auth_codes.items() if v is authorization_code),
            "",
        )
        if code:
            self._auth_codes.pop(code, None)
        if code and not helpers.is_oauth_storage_disabled():
            try:
                await helpers._oauth_delete_auth_code_row(code)
            except Exception as exc:
                if helpers._is_missing_oauth_table_error(exc):
                    helpers.disable_oauth_storage()
                else:
                    raise
        return await self._issue_token_pair(
            client_id=authorization_code.client_id,
            agent_id=authorization_code.agent_id,
            scope=client.scope or "patchcord",
        )

    async def _issue_token_pair(self, client_id: str, agent_id: str, scope: str) -> OAuthToken:
        from patchcord.server import helpers

        namespace_id, resolved_agent_id = self._resolve_identity_for_client(client_id)
        if resolved_agent_id != agent_id:
            agent_id = resolved_agent_id
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(48)
        access_expires_at = time.time() + OAUTH_ACCESS_TOKEN_TTL_SECONDS
        refresh_expires_at = time.time() + OAUTH_REFRESH_TOKEN_TTL_SECONDS

        self._issued_tokens[access_token] = _IssuedAccessToken(
            client_id=client_id,
            namespace_id=namespace_id,
            agent_id=agent_id,
            scope=scope,
            expires_at=access_expires_at,
        )
        self._refresh_tokens[refresh_token] = _StoredRefreshToken(
            token=refresh_token,
            client_id=client_id,
            namespace_id=namespace_id,
            agent_id=agent_id,
            scope=scope,
            expires_at=refresh_expires_at,
        )

        if not helpers.is_oauth_storage_disabled():
            try:
                await helpers._oauth_insert_access_token_row(
                    {
                        "access_token": access_token,
                        "client_id": client_id,
                        "namespace_id": namespace_id,
                        "agent_id": agent_id,
                        "scope": scope,
                        "expires_at": _iso_at(access_expires_at),
                    }
                )
                await helpers._oauth_insert_refresh_token_row(
                    {
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "namespace_id": namespace_id,
                        "agent_id": agent_id,
                        "scope": scope,
                        "expires_at": _iso_at(refresh_expires_at),
                    }
                )
            except Exception as exc:
                if helpers._is_missing_oauth_table_error(exc):
                    helpers.disable_oauth_storage()
                else:
                    raise

        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=OAUTH_ACCESS_TOKEN_TTL_SECONDS,
            scope=scope,
            refresh_token=refresh_token,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        from patchcord.server import helpers

        # 1. Check DB bearer tokens
        db_identity = await helpers.lookup_bearer_token(token)
        if db_identity:
            namespace_id, agent_id_val = db_identity
            return AccessToken(
                token=token,
                client_id=f"{namespace_id}:{agent_id_val}",
                scopes=["patchcord"],
            )

        # 3. Check OAuth issued tokens (in-memory)
        issued = self._issued_tokens.get(token)
        if issued and issued.expires_at > time.time():
            return AccessToken(
                token=token,
                client_id=f"{issued.namespace_id}:{issued.agent_id}",
                scopes=_scope_list(issued.scope),
                expires_at=int(issued.expires_at),
            )
        if issued:
            self._issued_tokens.pop(token, None)

        if helpers.is_oauth_storage_disabled():
            return None
        try:
            row = await helpers._oauth_get_access_token_row(token)
        except Exception as exc:
            if helpers._is_missing_oauth_table_error(exc):
                helpers.disable_oauth_storage()
                return None
            raise
        if not row:
            return None
        scope = clean(str(row.get("scope", ""))) or "patchcord"
        expires_at = parse_ts(row.get("expires_at"))
        if expires_at is None:
            return None
        return AccessToken(
            token=token,
            client_id=(
                f"{clean(str(row.get('namespace_id', ''))) or _OAUTH_DEFAULT_NAMESPACE}:"
                f"{clean(str(row.get('agent_id', '')))}"
            ),
            scopes=_scope_list(scope),
            expires_at=int(expires_at.timestamp()),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> _StoredRefreshToken | None:
        from patchcord.server import helpers

        stored = self._refresh_tokens.get(refresh_token)
        if stored and stored.expires_at <= time.time():
            self._refresh_tokens.pop(refresh_token, None)
            stored = None

        if stored is None:
            if not helpers.is_oauth_storage_disabled():
                try:
                    stored = await helpers._oauth_get_refresh_token_row(refresh_token)
                except Exception as exc:
                    if helpers._is_missing_oauth_table_error(exc):
                        helpers.disable_oauth_storage()
                        stored = None
                    else:
                        raise
                if stored is not None:
                    self._refresh_tokens[refresh_token] = stored

        if stored and stored.client_id == client.client_id:
            return stored
        return None

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: _StoredRefreshToken, scopes: list[str]
    ) -> OAuthToken:
        from patchcord.server import helpers

        self._refresh_tokens.pop(refresh_token.token, None)
        if not helpers.is_oauth_storage_disabled():
            try:
                await helpers._oauth_delete_refresh_token_row(refresh_token.token)
            except Exception as exc:
                if helpers._is_missing_oauth_table_error(exc):
                    helpers.disable_oauth_storage()
                else:
                    raise
        scope = " ".join(scopes).strip() or refresh_token.scope or client.scope or "patchcord"
        return await self._issue_token_pair(
            client_id=refresh_token.client_id,
            agent_id=refresh_token.agent_id,
            scope=scope,
        )

    async def revoke_token(self, token: AccessToken | None) -> None:
        from patchcord.server import helpers

        if not token:
            return
        self._issued_tokens.pop(token.token, None)
        if not helpers.is_oauth_storage_disabled():
            try:
                await helpers._oauth_delete_access_token_row(token.token)
            except Exception as exc:
                if helpers._is_missing_oauth_table_error(exc):
                    helpers.disable_oauth_storage()
                else:
                    raise
