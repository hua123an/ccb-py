"""Tests for ccb.mcp module."""
import pytest

from ccb.mcp.config_validator import validate_server_config, validate_all_configs
from ccb.mcp.auth import MCPAuthInfo, MCPAuthManager
from ccb.mcp.sampling import SamplingRequest, SamplingResponse, SamplingHandler


class TestConfigValidator:
    def test_valid_stdio(self):
        r = validate_server_config("test", {"type": "stdio", "command": "python3", "args": ["-m", "mcp"]})
        assert r.valid is True

    def test_missing_command(self):
        r = validate_server_config("test", {"type": "stdio"})
        assert r.valid is False
        assert any("command" in e.field for e in r.errors)

    def test_valid_http(self):
        r = validate_server_config("test", {"type": "http", "url": "http://localhost:8080"})
        assert r.valid is True

    def test_invalid_http_url(self):
        r = validate_server_config("test", {"type": "http", "url": "ftp://bad"})
        assert r.valid is False

    def test_missing_url(self):
        r = validate_server_config("test", {"type": "http"})
        assert r.valid is False

    def test_unknown_type(self):
        r = validate_server_config("test", {"type": "grpc"})
        assert r.valid is False

    def test_bad_args_type(self):
        r = validate_server_config("test", {"type": "stdio", "command": "test", "args": "not a list"})
        assert r.valid is False

    def test_validate_all(self):
        r = validate_all_configs({
            "good": {"type": "stdio", "command": "node"},
            "bad": {"type": "http"},
        })
        assert r.valid is False
        assert len(r.errors) >= 1

    def test_format(self):
        r = validate_server_config("test", {"type": "stdio"})
        text = r.format()
        assert "test" in text


class TestMCPAuth:
    def test_bearer_headers(self):
        auth = MCPAuthInfo(server_name="test", auth_type="bearer", token="abc123")
        headers = auth.get_headers()
        assert headers["Authorization"] == "Bearer abc123"

    def test_api_key_headers(self):
        auth = MCPAuthInfo(server_name="test", auth_type="api_key", api_key="key123")
        headers = auth.get_headers()
        assert headers["Authorization"] == "key123"

    def test_no_auth(self):
        auth = MCPAuthInfo(server_name="test", auth_type="none")
        assert auth.get_headers() == {}

    def test_expired(self):
        import time
        auth = MCPAuthInfo(server_name="test", expires_at=time.time() - 100)
        assert auth.expired is True

    def test_not_expired(self):
        import time
        auth = MCPAuthInfo(server_name="test", expires_at=time.time() + 3600)
        assert auth.expired is False

    def test_manager_set_and_get(self, tmp_path):
        mgr = MCPAuthManager()
        mgr._config_path = tmp_path / "mcp_auth.json"
        mgr.set_bearer_token("server1", "token123")
        auth = mgr.get_auth("server1")
        assert auth is not None
        assert auth.token == "token123"

    def test_manager_remove(self, tmp_path):
        mgr = MCPAuthManager()
        mgr._config_path = tmp_path / "mcp_auth.json"
        mgr.set_api_key("s1", "key")
        assert mgr.remove("s1") is True
        assert mgr.get_auth("s1") is None


class TestSampling:
    def test_request_creation(self):
        req = SamplingRequest(
            messages=[{"role": "user", "content": {"type": "text", "text": "Hello"}}],
            max_tokens=1024,
        )
        assert len(req.messages) == 1
        assert req.max_tokens == 1024

    def test_handler_disabled(self):
        handler = SamplingHandler()
        handler._enabled = False
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(handler.handle_request({}))
        assert "error" in result

    def test_handler_no_callback(self):
        handler = SamplingHandler()
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(handler.handle_request({}))
        assert "error" in result

    def test_toggle(self):
        handler = SamplingHandler()
        assert handler.toggle() is False
        assert handler.toggle() is True

    def test_stats(self):
        handler = SamplingHandler()
        stats = handler.stats
        assert stats["enabled"] is True
        assert stats["requests_handled"] == 0
