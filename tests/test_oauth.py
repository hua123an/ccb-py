"""Tests for ccb.oauth module."""
import json
import time
from pathlib import Path

import pytest

from ccb.oauth.token_store import OAuthToken, TokenStore
from ccb.oauth.flow import OAuthConfig, OAuthFlow, KNOWN_PROVIDERS
from ccb.oauth.client import OAuthClient


class TestOAuthToken:
    def test_not_expired(self):
        t = OAuthToken(access_token="abc", expires_at=time.time() + 3600)
        assert t.expired is False

    def test_expired(self):
        t = OAuthToken(access_token="abc", expires_at=time.time() - 100)
        assert t.expired is True

    def test_no_expiry(self):
        t = OAuthToken(access_token="abc", expires_at=0)
        assert t.expired is False

    def test_auth_header(self):
        t = OAuthToken(access_token="abc", token_type="Bearer")
        assert t.authorization_header == "Bearer abc"

    def test_serialization(self):
        t = OAuthToken(access_token="abc", refresh_token="def", provider="test")
        d = t.to_dict()
        t2 = OAuthToken.from_dict(d)
        assert t2.access_token == "abc"
        assert t2.refresh_token == "def"
        assert t2.provider == "test"


class TestTokenStore:
    def test_file_store_and_retrieve(self, tmp_path):
        store = TokenStore()
        store._backend = "encrypted_file"
        store._fallback_dir = tmp_path / "tokens"

        token = OAuthToken(access_token="secret123", provider="test")
        assert store.store("test:default", token) is True

        retrieved = store.retrieve("test:default")
        assert retrieved is not None
        assert retrieved.access_token == "secret123"

    def test_file_delete(self, tmp_path):
        store = TokenStore()
        store._backend = "encrypted_file"
        store._fallback_dir = tmp_path / "tokens"

        token = OAuthToken(access_token="todelete")
        store.store("del:key", token)
        assert store.delete("del:key") is True
        assert store.retrieve("del:key") is None

    def test_list_keys(self, tmp_path):
        store = TokenStore()
        store._backend = "encrypted_file"
        store._fallback_dir = tmp_path / "tokens"

        store.store("a:1", OAuthToken(access_token="a"))
        store.store("b:2", OAuthToken(access_token="b"))
        keys = store.list_keys()
        assert len(keys) >= 2

    def test_detect_backend(self):
        store = TokenStore()
        assert store.backend in ("keychain", "libsecret", "wincred", "encrypted_file")


class TestOAuthConfig:
    def test_known_providers(self):
        assert "github" in KNOWN_PROVIDERS
        assert "authorize_url" in KNOWN_PROVIDERS["github"]

    def test_config_creation(self):
        cfg = OAuthConfig(
            provider="test",
            client_id="abc",
            authorize_url="https://example.com/auth",
            token_url="https://example.com/token",
        )
        assert cfg.client_id == "abc"


class TestOAuthFlow:
    def test_pkce_generation(self):
        flow = OAuthFlow(OAuthConfig(
            provider="test",
            client_id="abc",
            authorize_url="https://example.com/auth",
            token_url="https://example.com/token",
        ))
        flow._generate_pkce()
        assert len(flow._code_verifier) > 40
        assert len(flow._code_challenge) > 20

    def test_authorization_url(self):
        flow = OAuthFlow(OAuthConfig(
            provider="test",
            client_id="abc",
            authorize_url="https://example.com/auth",
            token_url="https://example.com/token",
            scopes=["read", "write"],
        ))
        url = flow.get_authorization_url()
        assert "example.com/auth" in url
        assert "client_id=abc" in url
        assert "code_challenge" in url
        assert "S256" in url

    def test_state_generation(self):
        flow = OAuthFlow(OAuthConfig(provider="test", client_id="abc"))
        state = flow._generate_state()
        assert len(state) > 20


class TestOAuthClient:
    def test_init(self, tmp_path):
        # Create a config file
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "oauth.json").write_text(json.dumps({
            "github": {
                "client_id": "test_id",
                "client_secret": "test_secret",
            }
        }))

        # Monkeypatch home
        import ccb.oauth.client as client_module
        original_configs = OAuthClient._load_configs

        c = OAuthClient()
        # Just test it doesn't crash
        assert isinstance(c.configured_providers, list)

    def test_list_accounts(self, tmp_path):
        store = TokenStore()
        store._backend = "encrypted_file"
        store._fallback_dir = tmp_path / "tokens"

        store.store("github:user1", OAuthToken(
            access_token="abc", provider="github", account="user1",
            expires_at=time.time() + 3600,
        ))

        client = OAuthClient(store=store)
        accounts = client.list_accounts()
        assert len(accounts) >= 1

    def test_is_authenticated(self, tmp_path):
        store = TokenStore()
        store._backend = "encrypted_file"
        store._fallback_dir = tmp_path / "tokens"

        store.store("test:default", OAuthToken(
            access_token="abc", expires_at=time.time() + 3600,
        ))

        client = OAuthClient(store=store)
        assert client.is_authenticated("test") is True
        assert client.is_authenticated("nonexistent") is False
