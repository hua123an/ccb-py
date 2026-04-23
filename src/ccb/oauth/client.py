"""High-level OAuth client that ties together flows, token storage, and auto-refresh.

Usage:
    client = get_oauth_client()
    token = await client.login("anthropic")  # Opens browser
    token = await client.ensure_valid("anthropic")  # Auto-refresh if expired
    await client.logout("anthropic")
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from ccb.oauth.flow import OAuthConfig, OAuthFlow, KNOWN_PROVIDERS
from ccb.oauth.token_store import OAuthToken, TokenStore, get_token_store


class OAuthClient:
    """Manages OAuth sessions across multiple providers."""

    def __init__(self, store: TokenStore | None = None) -> None:
        self._store = store or get_token_store()
        self._flows: dict[str, OAuthFlow] = {}
        self._configs: dict[str, OAuthConfig] = {}
        self._load_configs()

    def _load_configs(self) -> None:
        """Load OAuth client configs from ~/.claude/oauth.json."""
        config_path = Path.home() / ".claude" / "oauth.json"
        if not config_path.exists():
            return
        try:
            data = json.loads(config_path.read_text())
            for provider, cfg in data.items():
                known = KNOWN_PROVIDERS.get(provider, {})
                merged = {**known, **cfg, "provider": provider}
                self._configs[provider] = OAuthConfig(**{
                    k: v for k, v in merged.items()
                    if k in OAuthConfig.__dataclass_fields__
                })
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    def _save_configs(self) -> None:
        config_path = Path.home() / ".claude" / "oauth.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for provider, cfg in self._configs.items():
            data[provider] = {
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
                "authorize_url": cfg.authorize_url,
                "token_url": cfg.token_url,
                "scopes": cfg.scopes,
            }
        config_path.write_text(json.dumps(data, indent=2))

    def configure_provider(
        self,
        provider: str,
        client_id: str,
        client_secret: str = "",
        **kwargs: Any,
    ) -> None:
        """Register/update an OAuth provider configuration."""
        known = KNOWN_PROVIDERS.get(provider, {})
        merged = {**known, **kwargs, "provider": provider, "client_id": client_id, "client_secret": client_secret}
        self._configs[provider] = OAuthConfig(**{
            k: v for k, v in merged.items()
            if k in OAuthConfig.__dataclass_fields__
        })
        self._save_configs()

    def _get_flow(self, provider: str) -> OAuthFlow:
        if provider not in self._flows:
            config = self._configs.get(provider)
            if not config:
                # Check if known provider with no client_id configured
                if provider in KNOWN_PROVIDERS:
                    raise ValueError(
                        f"Provider '{provider}' not configured. Run:\n"
                        f"  /login {provider} --client-id <YOUR_CLIENT_ID>"
                    )
                raise ValueError(f"Unknown OAuth provider: {provider}")
            self._flows[provider] = OAuthFlow(config)
        return self._flows[provider]

    # ── Login ──

    async def login(
        self,
        provider: str,
        method: str = "auto",
        account: str = "default",
    ) -> OAuthToken:
        """Authenticate with a provider.

        method: "auto" (try browser, fallback device), "browser", "device", "client_credentials"
        """
        flow = self._get_flow(provider)

        if method == "client_credentials":
            token = await flow.client_credentials_flow()
        elif method == "device":
            token = await flow.device_code_flow()
            if not token:
                raise ValueError("Device code flow timed out")
        elif method == "browser":
            token = await flow.authorization_code_flow()
            if not token:
                raise ValueError("Authorization code flow timed out")
        else:  # auto
            try:
                token = await flow.authorization_code_flow(timeout=120)
                if not token:
                    raise TimeoutError()
            except Exception:
                # Fallback to device code if browser flow fails
                if flow.config.device_code_url:
                    token = await flow.device_code_flow()
                    if not token:
                        raise ValueError("All OAuth flows failed")
                else:
                    raise

        token.provider = provider
        token.account = account

        # Store securely
        key = f"{provider}:{account}"
        self._store.store(key, token)

        return token

    # ── Logout ──

    async def logout(self, provider: str, account: str = "default") -> bool:
        """Revoke and delete token for a provider."""
        key = f"{provider}:{account}"
        token = self._store.retrieve(key)
        if token:
            try:
                flow = self._get_flow(provider)
                await flow.revoke_token(token)
            except Exception:
                pass  # Best effort revocation
            return self._store.delete(key)
        return False

    # ── Token management ──

    def get_token(self, provider: str, account: str = "default") -> OAuthToken | None:
        """Get stored token (may be expired)."""
        key = f"{provider}:{account}"
        return self._store.retrieve(key)

    async def ensure_valid(self, provider: str, account: str = "default") -> OAuthToken | None:
        """Get a valid token, refreshing if needed."""
        token = self.get_token(provider, account)
        if not token:
            return None

        if not token.expired:
            return token

        # Try refresh
        if token.refresh_token:
            try:
                flow = self._get_flow(provider)
                new_token = await flow.refresh_token(token)
                new_token.account = account
                key = f"{provider}:{account}"
                self._store.store(key, new_token)
                return new_token
            except Exception:
                pass

        return None  # Token expired and refresh failed

    async def get_access_token(self, provider: str, account: str = "default") -> str | None:
        """Get a valid access token string, or None."""
        token = await self.ensure_valid(provider, account)
        return token.access_token if token else None

    # ── User info ──

    async def get_user_info(self, provider: str, account: str = "default") -> dict[str, Any]:
        """Get user profile for an authenticated provider."""
        token = await self.ensure_valid(provider, account)
        if not token:
            return {"error": "Not authenticated"}
        flow = self._get_flow(provider)
        return await flow.get_user_info(token)

    # ── Status ──

    def list_accounts(self) -> list[dict[str, Any]]:
        """List all stored OAuth accounts."""
        accounts = []
        for key in self._store.list_keys():
            token = self._store.retrieve(key)
            if token:
                parts = key.split(":", 1)
                accounts.append({
                    "key": key,
                    "provider": parts[0] if parts else "",
                    "account": parts[1] if len(parts) > 1 else "default",
                    "expired": token.expired,
                    "scope": token.scope,
                    "has_refresh": bool(token.refresh_token),
                })
        return accounts

    def is_authenticated(self, provider: str, account: str = "default") -> bool:
        token = self.get_token(provider, account)
        return token is not None and not token.expired

    @property
    def configured_providers(self) -> list[str]:
        return list(self._configs.keys())


# Module singleton
_client: OAuthClient | None = None


def get_oauth_client() -> OAuthClient:
    global _client
    if _client is None:
        _client = OAuthClient()
    return _client
