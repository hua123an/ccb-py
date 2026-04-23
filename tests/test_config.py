"""Tests for ccb.config module."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ccb.config import (
    load_global_config,
    get_global_config,
    save_global_config,
    load_settings,
    get_settings,
    save_settings,
    get_project_config,
    save_project_config,
    project_config_key,
    load_accounts,
    get_account_names,
    switch_account,
    get_api_key,
    get_model,
    get_provider,
    get_permission_mode,
    has_completed_onboarding,
    complete_onboarding,
)


@pytest.fixture(autouse=True)
def reset_config():
    """Reset global config state between tests."""
    import ccb.config as cfg_mod
    cfg_mod._global_config = {}
    cfg_mod._active_account = None
    yield
    cfg_mod._global_config = {}
    cfg_mod._active_account = None


class TestGlobalConfig:
    def test_load_missing(self, tmp_path):
        with patch("ccb.config.claude_json_path", return_value=tmp_path / "missing.json"):
            cfg = load_global_config()
            assert cfg == {}

    def test_load_valid(self, tmp_path):
        p = tmp_path / "claude.json"
        p.write_text('{"model": "gpt-4", "hasCompletedOnboarding": true}')
        with patch("ccb.config.claude_json_path", return_value=p):
            cfg = load_global_config()
            assert cfg["model"] == "gpt-4"

    def test_load_corrupted(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json!!!")
        with patch("ccb.config.claude_json_path", return_value=p):
            cfg = load_global_config()
            assert cfg == {}

    def test_save(self, tmp_path):
        p = tmp_path / "claude.json"
        with patch("ccb.config.claude_json_path", return_value=p):
            save_global_config({"key": "value"})
            assert p.exists()
            assert json.loads(p.read_text())["key"] == "value"


class TestSettings:
    def test_load_missing(self, tmp_path):
        with patch("ccb.config.settings_path", return_value=tmp_path / "missing.json"):
            s = load_settings()
            assert s == {}

    def test_load_valid(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text('{"theme": "dark"}')
        with patch("ccb.config.settings_path", return_value=p):
            s = load_settings()
            assert s["theme"] == "dark"

    def test_save(self, tmp_path):
        p = tmp_path / "settings.json"
        with patch("ccb.config.settings_path", return_value=p):
            save_settings({"theme": "light"})
            assert json.loads(p.read_text())["theme"] == "light"


class TestProjectConfig:
    def test_key_generation(self):
        key = project_config_key("/home/user/project")
        assert "/" not in key

    def test_get_empty(self, tmp_path):
        p = tmp_path / "claude.json"
        p.write_text("{}")
        with patch("ccb.config.claude_json_path", return_value=p):
            load_global_config()
            cfg = get_project_config("/tmp/myproject")
            assert cfg == {}


class TestAccounts:
    def test_load_missing(self, tmp_path):
        with patch("ccb.config.accounts_path", return_value=tmp_path / "missing.json"):
            accts = load_accounts()
            assert accts == {"accounts": {}}

    def test_load_valid(self, tmp_path):
        p = tmp_path / "accounts.json"
        p.write_text(json.dumps({
            "active": "default",
            "accounts": {
                "default": {"apiKey": "sk-123", "provider": "anthropic"},
                "work": {"apiKey": "sk-456", "provider": "openai"},
            }
        }))
        with patch("ccb.config.accounts_path", return_value=p):
            names = get_account_names()
            assert "default" in names
            assert "work" in names

    def test_switch_account(self, tmp_path):
        p = tmp_path / "accounts.json"
        p.write_text(json.dumps({
            "active": "default",
            "accounts": {
                "default": {"apiKey": "sk-123"},
                "work": {"apiKey": "sk-456"},
            }
        }))
        with patch("ccb.config.accounts_path", return_value=p):
            assert switch_account("work") is True
            data = json.loads(p.read_text())
            assert data["active"] == "work"

    def test_switch_nonexistent(self, tmp_path):
        p = tmp_path / "accounts.json"
        p.write_text(json.dumps({"accounts": {}}))
        with patch("ccb.config.accounts_path", return_value=p):
            assert switch_account("nonexistent") is False


class TestApiResolution:
    def test_api_key_from_env(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-env-key"}):
            assert get_api_key() == "sk-env-key"

    def test_model_from_env(self):
        with patch.dict(os.environ, {"ANTHROPIC_MODEL": "claude-3-haiku"}):
            assert get_model() == "claude-3-haiku"

    def test_provider_from_openai_env(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai"}, clear=False):
            import ccb.config as cfg_mod
            cfg_mod._active_account = None
            p = get_provider()
            assert p == "openai"


class TestPermissionMode:
    def test_default(self, tmp_path):
        with patch("ccb.config.settings_path", return_value=tmp_path / "s.json"):
            assert get_permission_mode() == "default"

    def test_bypass(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}))
        with patch("ccb.config.settings_path", return_value=p):
            assert get_permission_mode() == "bypassPermissions"


class TestOnboarding:
    def test_not_completed(self, tmp_path):
        p = tmp_path / "claude.json"
        p.write_text("{}")
        with patch("ccb.config.claude_json_path", return_value=p):
            load_global_config()
            assert has_completed_onboarding() is False

    def test_completed(self, tmp_path):
        p = tmp_path / "claude.json"
        p.write_text('{"hasCompletedOnboarding": true}')
        with patch("ccb.config.claude_json_path", return_value=p):
            load_global_config()
            assert has_completed_onboarding() is True
