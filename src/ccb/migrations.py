"""Configuration migrations for ccb-py.

Handles upgrading config file formats across versions.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

CURRENT_VERSION = 2


def _config_dir() -> Path:
    return Path.home() / ".claude"


def _backup(path: Path) -> Path:
    backup = path.with_suffix(f".bak.{int(time.time())}")
    shutil.copy2(path, backup)
    return backup


# ---------------------------------------------------------------------------
# Migration functions
# ---------------------------------------------------------------------------

def _migrate_v0_to_v1(data: dict[str, Any]) -> dict[str, Any]:
    """v0 → v1: Normalize provider config keys."""
    if "api_key" in data and "providers" not in data:
        provider = data.pop("provider", "anthropic")
        api_key = data.pop("api_key", "")
        api_base = data.pop("api_base", "")
        data["providers"] = {
            provider: {
                "api_key": api_key,
                **({"api_base": api_base} if api_base else {}),
            }
        }
    data["_config_version"] = 1
    return data


def _migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """v1 → v2: Add settings sections."""
    if "settings" not in data:
        settings: dict[str, Any] = {}
        # Move top-level settings into nested structure
        for key in ("theme", "vim_mode", "output_style", "sandbox", "multi_pass"):
            if key in data:
                settings[key] = data.pop(key)
        data["settings"] = settings
    data["_config_version"] = 2
    return data


_MIGRATIONS = [
    (0, 1, _migrate_v0_to_v1),
    (1, 2, _migrate_v1_to_v2),
]


def migrate_config(path: Path | None = None) -> dict[str, Any]:
    """Load and migrate a config file to the current version."""
    path = path or (_config_dir() / "config.json")
    if not path.exists():
        return {"_config_version": CURRENT_VERSION}

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"_config_version": CURRENT_VERSION}

    version = data.get("_config_version", 0)
    if version >= CURRENT_VERSION:
        return data

    # Backup before migrating
    _backup(path)

    for from_v, to_v, fn in _MIGRATIONS:
        if version == from_v:
            data = fn(data)
            version = to_v

    # Save migrated config
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return data


def migrate_installed_plugins(path: Path | None = None) -> dict[str, Any]:
    """Migrate installed_plugins.json format if needed."""
    path = path or (_config_dir() / "plugins" / "installed_plugins.json")
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    # v2 format has {"version": 2, "plugins": {...}}
    if isinstance(data, dict) and data.get("version") == 2:
        return data  # already current

    # v1 / flat format: convert to v2
    if isinstance(data, dict) and "version" not in data:
        _backup(path)
        v2 = {"version": 2, "plugins": {}}
        for key, val in data.items():
            if isinstance(val, dict):
                v2["plugins"][key] = [val]
        path.write_text(json.dumps(v2, indent=2, ensure_ascii=False))
        return v2

    return data


def run_all_migrations() -> list[str]:
    """Run all pending migrations. Returns list of actions taken."""
    actions = []

    cfg_path = _config_dir() / "config.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text())
            v = data.get("_config_version", 0)
            if v < CURRENT_VERSION:
                migrate_config(cfg_path)
                actions.append(f"config.json: v{v} → v{CURRENT_VERSION}")
        except Exception:
            pass

    plugins_path = _config_dir() / "plugins" / "installed_plugins.json"
    if plugins_path.exists():
        try:
            data = json.loads(plugins_path.read_text())
            if isinstance(data, dict) and "version" not in data:
                migrate_installed_plugins(plugins_path)
                actions.append("installed_plugins.json: flat → v2")
        except Exception:
            pass

    return actions
