"""MCP OAuth authentication.

Implements the MCP auth spec:
- Resource-based auth info discovery
- Token injection into MCP requests
- Auto-refresh of MCP tokens
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MCPAuthInfo:
    """Auth configuration for an MCP server."""
    server_name: str
    auth_type: str = "none"  # "none", "bearer", "api_key", "oauth"
    token: str = ""
    api_key: str = ""
    header_name: str = "Authorization"
    header_prefix: str = "Bearer"
    oauth_client_id: str = ""
    oauth_provider: str = ""
    expires_at: float = 0.0

    @property
    def expired(self) -> bool:
        if self.expires_at <= 0:
            return False
        return time.time() >= self.expires_at - 30

    def get_headers(self) -> dict[str, str]:
        if self.auth_type == "bearer" and self.token:
            return {self.header_name: f"{self.header_prefix} {self.token}"}
        elif self.auth_type == "api_key" and self.api_key:
            return {self.header_name: self.api_key}
        elif self.auth_type == "oauth" and self.token:
            return {self.header_name: f"Bearer {self.token}"}
        return {}


class MCPAuthManager:
    """Manages auth for MCP server connections."""

    def __init__(self) -> None:
        self._auths: dict[str, MCPAuthInfo] = {}
        self._config_path = Path.home() / ".claude" / "mcp_auth.json"
        self._load()

    def _load(self) -> None:
        if not self._config_path.exists():
            return
        try:
            data = json.loads(self._config_path.read_text())
            for name, cfg in data.items():
                self._auths[name] = MCPAuthInfo(server_name=name, **{
                    k: v for k, v in cfg.items()
                    if k in MCPAuthInfo.__dataclass_fields__ and k != "server_name"
                })
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    def _save(self) -> None:
        data = {}
        for name, auth in self._auths.items():
            data[name] = {
                "auth_type": auth.auth_type,
                "token": auth.token,
                "api_key": auth.api_key,
                "header_name": auth.header_name,
                "header_prefix": auth.header_prefix,
                "oauth_client_id": auth.oauth_client_id,
                "oauth_provider": auth.oauth_provider,
                "expires_at": auth.expires_at,
            }
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(json.dumps(data, indent=2))

    def set_bearer_token(self, server_name: str, token: str, expires_at: float = 0) -> None:
        self._auths[server_name] = MCPAuthInfo(
            server_name=server_name,
            auth_type="bearer",
            token=token,
            expires_at=expires_at,
        )
        self._save()

    def set_api_key(self, server_name: str, api_key: str, header_name: str = "Authorization") -> None:
        self._auths[server_name] = MCPAuthInfo(
            server_name=server_name,
            auth_type="api_key",
            api_key=api_key,
            header_name=header_name,
        )
        self._save()

    def set_oauth(self, server_name: str, provider: str, client_id: str) -> None:
        self._auths[server_name] = MCPAuthInfo(
            server_name=server_name,
            auth_type="oauth",
            oauth_provider=provider,
            oauth_client_id=client_id,
        )
        self._save()

    def get_auth(self, server_name: str) -> MCPAuthInfo | None:
        return self._auths.get(server_name)

    def get_headers(self, server_name: str) -> dict[str, str]:
        auth = self._auths.get(server_name)
        return auth.get_headers() if auth else {}

    def remove(self, server_name: str) -> bool:
        if server_name in self._auths:
            del self._auths[server_name]
            self._save()
            return True
        return False

    async def ensure_valid(self, server_name: str) -> dict[str, str]:
        """Get valid auth headers, refreshing OAuth if needed."""
        auth = self._auths.get(server_name)
        if not auth:
            return {}

        if auth.auth_type == "oauth" and auth.expired and auth.oauth_provider:
            try:
                from ccb.oauth import get_oauth_client
                client = get_oauth_client()
                token = await client.ensure_valid(auth.oauth_provider)
                if token:
                    auth.token = token.access_token
                    auth.expires_at = token.expires_at
                    self._save()
            except Exception:
                pass

        return auth.get_headers()

    def list_configured(self) -> list[dict[str, Any]]:
        return [
            {
                "server": name,
                "auth_type": auth.auth_type,
                "expired": auth.expired,
                "has_token": bool(auth.token or auth.api_key),
            }
            for name, auth in self._auths.items()
        ]


_manager: MCPAuthManager | None = None


def get_mcp_auth_manager() -> MCPAuthManager:
    global _manager
    if _manager is None:
        _manager = MCPAuthManager()
    return _manager
