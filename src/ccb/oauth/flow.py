"""OAuth 2.0 authorization flows for ccb-py.

Implements:
- Authorization Code flow with PKCE (for interactive login)
- Device Code flow (for headless/SSH environments)
- Client Credentials flow (for service accounts)
- Token refresh flow
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Any

from ccb.oauth.token_store import OAuthToken


@dataclass
class OAuthConfig:
    """OAuth provider configuration."""
    provider: str             # "anthropic", "github", etc.
    client_id: str
    client_secret: str = ""   # Empty for public clients (PKCE)
    authorize_url: str = ""
    token_url: str = ""
    device_code_url: str = ""
    revoke_url: str = ""
    userinfo_url: str = ""
    scopes: list[str] | None = None
    redirect_uri: str = "http://localhost:9876/callback"


# Known provider configurations
KNOWN_PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "authorize_url": "https://console.anthropic.com/oauth/authorize",
        "token_url": "https://console.anthropic.com/oauth/token",
        "revoke_url": "https://console.anthropic.com/oauth/revoke",
        "userinfo_url": "https://api.anthropic.com/v1/me",
        "scopes": ["user:read", "model:read", "model:write"],
    },
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "device_code_url": "https://github.com/login/device/code",
        "userinfo_url": "https://api.github.com/user",
        "scopes": ["repo", "read:org"],
    },
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "revoke_url": "https://oauth2.googleapis.com/revoke",
        "userinfo_url": "https://www.googleapis.com/oauth2/v3/userinfo",
        "scopes": ["openid", "email", "profile"],
    },
}


class OAuthFlow:
    """Manages OAuth 2.0 authorization flows."""

    def __init__(self, config: OAuthConfig) -> None:
        self.config = config
        self._state: str = ""
        self._code_verifier: str = ""
        self._code_challenge: str = ""

    # ── PKCE helpers ──

    def _generate_pkce(self) -> None:
        """Generate PKCE code_verifier and code_challenge."""
        self._code_verifier = secrets.token_urlsafe(64)[:128]
        digest = hashlib.sha256(self._code_verifier.encode()).digest()
        self._code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def _generate_state(self) -> str:
        self._state = secrets.token_urlsafe(32)
        return self._state

    # ── Authorization Code + PKCE ──

    def get_authorization_url(self) -> str:
        """Build the authorization URL for the browser."""
        self._generate_pkce()
        state = self._generate_state()

        params = {
            "client_id": self.config.client_id,
            "response_type": "code",
            "redirect_uri": self.config.redirect_uri,
            "state": state,
            "code_challenge": self._code_challenge,
            "code_challenge_method": "S256",
        }
        if self.config.scopes:
            params["scope"] = " ".join(self.config.scopes)

        return f"{self.config.authorize_url}?{urllib.parse.urlencode(params)}"

    async def authorization_code_flow(self, timeout: int = 120) -> OAuthToken | None:
        """Run the full authorization code flow with local callback server.

        1. Opens browser to authorization URL
        2. Starts local HTTP server to receive callback
        3. Exchanges code for tokens
        """
        auth_url = self.get_authorization_url()

        # Start local callback server
        result: dict[str, str] = {}
        event = threading.Event()

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self_handler) -> None:
                query = urllib.parse.urlparse(self_handler.path).query
                params = urllib.parse.parse_qs(query)
                result["code"] = params.get("code", [""])[0]
                result["state"] = params.get("state", [""])[0]
                result["error"] = params.get("error", [""])[0]

                # Send success page
                self_handler.send_response(200)
                self_handler.send_header("Content-Type", "text/html")
                self_handler.end_headers()
                if result.get("error"):
                    body = f"<h1>Authentication Failed</h1><p>{result['error']}</p>"
                else:
                    body = (
                        "<h1>Authentication Successful!</h1>"
                        "<p>You can close this window and return to the terminal.</p>"
                        "<script>window.close()</script>"
                    )
                self_handler.wfile.write(body.encode())
                event.set()

            def log_message(self_handler, format: str, *args: Any) -> None:
                pass  # Suppress server logs

        # Parse port from redirect_uri
        parsed = urllib.parse.urlparse(self.config.redirect_uri)
        port = parsed.port or 9876

        server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            # Open browser
            webbrowser.open(auth_url)

            # Wait for callback
            if not event.wait(timeout=timeout):
                return None  # Timeout

            # Validate state
            if result.get("state") != self._state:
                raise ValueError("OAuth state mismatch — possible CSRF attack")

            if result.get("error"):
                raise ValueError(f"OAuth error: {result['error']}")

            code = result.get("code", "")
            if not code:
                return None

            # Exchange code for tokens
            return await self._exchange_code(code)

        finally:
            server.shutdown()

    async def _exchange_code(self, code: str) -> OAuthToken:
        """Exchange authorization code for tokens."""
        import aiohttp

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "client_id": self.config.client_id,
            "code_verifier": self._code_verifier,
        }
        if self.config.client_secret:
            payload["client_secret"] = self.config.client_secret

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.config.token_url,
                data=payload,
                headers={"Accept": "application/json"},
            ) as resp:
                data = await resp.json()

        if "error" in data:
            raise ValueError(f"Token exchange failed: {data.get('error_description', data['error'])}")

        return OAuthToken(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            refresh_token=data.get("refresh_token", ""),
            expires_at=time.time() + data.get("expires_in", 3600),
            scope=data.get("scope", ""),
            id_token=data.get("id_token", ""),
            provider=self.config.provider,
        )

    # ── Device Code flow (for headless/SSH) ──

    async def device_code_flow(self, poll_interval: int = 5, timeout: int = 300) -> OAuthToken | None:
        """Device authorization grant — for terminals without browser access.

        1. Request device code
        2. Display user_code and verification_uri
        3. Poll for completion
        """
        if not self.config.device_code_url:
            raise ValueError(f"Provider {self.config.provider} does not support device code flow")

        import aiohttp

        # Step 1: Request device code
        async with aiohttp.ClientSession() as session:
            payload = {"client_id": self.config.client_id}
            if self.config.scopes:
                payload["scope"] = " ".join(self.config.scopes)

            async with session.post(
                self.config.device_code_url,
                data=payload,
                headers={"Accept": "application/json"},
            ) as resp:
                data = await resp.json()

        device_code = data.get("device_code", "")
        user_code = data.get("user_code", "")
        verification_uri = data.get("verification_uri", data.get("verification_url", ""))
        interval = data.get("interval", poll_interval)
        expires_in = data.get("expires_in", timeout)

        if not device_code or not verification_uri:
            raise ValueError("Invalid device code response")

        # Step 2: Display to user
        print(f"\n  Open: {verification_uri}")
        print(f"  Enter code: {user_code}\n")

        # Try to open browser
        try:
            complete_uri = data.get("verification_uri_complete", "")
            if complete_uri:
                webbrowser.open(complete_uri)
            else:
                webbrowser.open(verification_uri)
        except Exception:
            pass

        # Step 3: Poll for token
        deadline = time.time() + expires_in
        async with aiohttp.ClientSession() as session:
            while time.time() < deadline:
                await asyncio.sleep(interval)

                async with session.post(
                    self.config.token_url,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": self.config.client_id,
                    },
                    headers={"Accept": "application/json"},
                ) as resp:
                    result = await resp.json()

                error = result.get("error", "")
                if error == "authorization_pending":
                    continue
                elif error == "slow_down":
                    interval += 5
                    continue
                elif error:
                    raise ValueError(f"Device code error: {error}")

                # Success
                return OAuthToken(
                    access_token=result["access_token"],
                    token_type=result.get("token_type", "Bearer"),
                    refresh_token=result.get("refresh_token", ""),
                    expires_at=time.time() + result.get("expires_in", 3600),
                    scope=result.get("scope", ""),
                    provider=self.config.provider,
                )

        return None  # Timeout

    # ── Token Refresh ──

    async def refresh_token(self, token: OAuthToken) -> OAuthToken:
        """Refresh an expired token."""
        if not token.refresh_token:
            raise ValueError("No refresh token available")

        import aiohttp

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": self.config.client_id,
        }
        if self.config.client_secret:
            payload["client_secret"] = self.config.client_secret

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.config.token_url,
                data=payload,
                headers={"Accept": "application/json"},
            ) as resp:
                data = await resp.json()

        if "error" in data:
            raise ValueError(f"Token refresh failed: {data.get('error_description', data['error'])}")

        return OAuthToken(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            refresh_token=data.get("refresh_token", token.refresh_token),
            expires_at=time.time() + data.get("expires_in", 3600),
            scope=data.get("scope", token.scope),
            id_token=data.get("id_token", ""),
            provider=token.provider,
            account=token.account,
        )

    # ── Revoke ──

    async def revoke_token(self, token: OAuthToken) -> bool:
        """Revoke a token."""
        if not self.config.revoke_url:
            return False

        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.config.revoke_url,
                data={"token": token.access_token, "client_id": self.config.client_id},
            ) as resp:
                return resp.status in (200, 204)

    # ── User Info ──

    async def get_user_info(self, token: OAuthToken) -> dict[str, Any]:
        """Fetch user profile from the provider."""
        if not self.config.userinfo_url:
            return {}

        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(
                self.config.userinfo_url,
                headers={"Authorization": token.authorization_header},
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"error": f"HTTP {resp.status}"}

    # ── Client Credentials (service account) ──

    async def client_credentials_flow(self) -> OAuthToken:
        """Client credentials grant for service-to-service auth."""
        if not self.config.client_secret:
            raise ValueError("client_secret required for client credentials flow")

        import aiohttp

        payload = {
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        if self.config.scopes:
            payload["scope"] = " ".join(self.config.scopes)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.config.token_url,
                data=payload,
                headers={"Accept": "application/json"},
            ) as resp:
                data = await resp.json()

        if "error" in data:
            raise ValueError(f"Client credentials failed: {data['error']}")

        return OAuthToken(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_at=time.time() + data.get("expires_in", 3600),
            scope=data.get("scope", ""),
            provider=self.config.provider,
        )
