"""Configuration management. Reads from env vars, ~/.claude.json, and ~/.claude/settings.json."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_global_config: dict[str, Any] = {}
_project_configs: dict[str, dict[str, Any]] = {}
_active_account: dict[str, Any] | None = None


def claude_dir() -> Path:
    return Path.home() / ".claude"


def claude_json_path() -> Path:
    return Path.home() / ".claude.json"


def settings_path() -> Path:
    return claude_dir() / "settings.json"


def project_config_key(cwd: str) -> str:
    return cwd.replace("/", "-").replace("\\", "-")


# ---------------------------------------------------------------------------
# Global config (~/.claude.json)
# ---------------------------------------------------------------------------
def load_global_config() -> dict[str, Any]:
    global _global_config
    p = claude_json_path()
    if p.exists():
        try:
            _global_config = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            _global_config = {}
    return _global_config


def get_global_config() -> dict[str, Any]:
    if not _global_config:
        load_global_config()
    return _global_config


def save_global_config(cfg: dict[str, Any]) -> None:
    global _global_config
    _global_config = cfg
    claude_json_path().write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Settings (~/.claude/settings.json)
# ---------------------------------------------------------------------------
def load_settings() -> dict[str, Any]:
    p = settings_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def get_settings() -> dict[str, Any]:
    """Alias for load_settings."""
    return load_settings()


def save_settings(settings: dict[str, Any]) -> None:
    settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings_path().write_text(json.dumps(settings, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Project config (~/.claude.json -> projects.<key>)
# ---------------------------------------------------------------------------
def get_project_config(cwd: str) -> dict[str, Any]:
    cfg = get_global_config()
    key = project_config_key(cwd)
    projects = cfg.get("projects", {})
    return projects.get(key, {})


def save_project_config(cwd: str, project_cfg: dict[str, Any]) -> None:
    cfg = get_global_config()
    key = project_config_key(cwd)
    cfg.setdefault("projects", {})[key] = project_cfg
    save_global_config(cfg)


# ---------------------------------------------------------------------------
# Accounts (~/.claude/accounts.json)
# ---------------------------------------------------------------------------
def accounts_path() -> Path:
    return claude_dir() / "accounts.json"


def load_accounts() -> dict[str, Any]:
    p = accounts_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {"accounts": {}}
    return {"accounts": {}}


def get_active_account() -> dict[str, Any] | None:
    """Get the active account profile from accounts.json."""
    global _active_account
    if _active_account is not None:
        return _active_account
    store = load_accounts()
    active_name = store.get("active")
    if active_name:
        _active_account = store.get("accounts", {}).get(active_name)
        if _active_account:
            _active_account["_name"] = active_name
            _active_account["_activeModel"] = store.get("activeModel")
        return _active_account
    return None


def get_account_names() -> list[str]:
    store = load_accounts()
    return list(store.get("accounts", {}).keys())


def switch_account(name: str, model: str | None = None) -> bool:
    store = load_accounts()
    if name not in store.get("accounts", {}):
        return False
    old_active = store.get("active")
    store["active"] = name
    if model:
        store["activeModel"] = model
    elif name != old_active:
        # Switching to a different account: clear activeModel so defaultModel takes effect
        store.pop("activeModel", None)
    # If same account and no model override, keep activeModel as-is
    accounts_path().write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n")
    global _active_account
    _active_account = None  # Force reload
    return True


# ---------------------------------------------------------------------------
# API key / model resolution
# ---------------------------------------------------------------------------
def get_api_key() -> str:
    """Resolve API key: env var > active account > settings > empty."""
    for env_key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        val = os.environ.get(env_key, "")
        if val:
            return val
    acct = get_active_account()
    if acct:
        return acct.get("apiKey", "")
    settings = load_settings()
    return settings.get("apiKey", "")


def get_model() -> str:
    """Resolve model: env var > active account model > settings > default."""
    if m := os.environ.get("ANTHROPIC_MODEL"):
        return m
    if m := os.environ.get("OPENAI_MODEL"):
        return m
    acct = get_active_account()
    if acct:
        active_model = acct.get("_activeModel")
        if active_model:
            return active_model
        return acct.get("defaultModel", "")
    settings = load_settings()
    return settings.get("model", "claude-sonnet-4-20250514")


def get_base_url() -> str | None:
    """Resolve base URL: env var > active account > None."""
    if url := os.environ.get("ANTHROPIC_BASE_URL"):
        return url
    if url := os.environ.get("OPENAI_BASE_URL"):
        return url
    acct = get_active_account()
    if acct:
        return acct.get("baseUrl")
    return None


def get_provider() -> str:
    """Determine provider: anthropic or openai."""
    acct = get_active_account()
    if acct:
        return acct.get("provider", "openai")
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL"):
        return "openai"
    model = get_model()
    if model.startswith(("gpt-", "o1-", "o3-", "o4-")):
        return "openai"
    return "anthropic"


def get_permission_mode() -> str:
    """Get permission mode: default, bypassPermissions, plan."""
    settings = load_settings()
    return settings.get("permissions", {}).get("defaultMode", "default")


def has_completed_onboarding() -> bool:
    cfg = get_global_config()
    return cfg.get("hasCompletedOnboarding", False)


def complete_onboarding() -> None:
    cfg = get_global_config()
    cfg["hasCompletedOnboarding"] = True
    save_global_config(cfg)
