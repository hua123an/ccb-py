"""Settings sync for ccb-py.

Synchronizes settings across devices using a git-backed store
or simple file-based sync.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any


SYNC_FILES = [
    "settings.json",
    "accounts.json",
    "keybindings.json",
    "buddy.json",
    "plugins/known_marketplaces.json",
    "plugins/installed_plugins.json",
]


class SettingsSync:
    """Sync settings via a shared directory or git repo."""

    def __init__(self) -> None:
        self._config_dir = Path.home() / ".claude"
        self._sync_dir = self._config_dir / "sync"
        self._sync_file = self._config_dir / "sync_config.json"
        self._config: dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        if self._sync_file.exists():
            try:
                self._config = json.loads(self._sync_file.read_text())
            except (json.JSONDecodeError, OSError):
                self._config = {}

    def _save_config(self) -> None:
        self._sync_file.write_text(json.dumps(self._config, indent=2))

    @property
    def enabled(self) -> bool:
        return self._config.get("enabled", False)

    @property
    def sync_method(self) -> str:
        return self._config.get("method", "directory")

    @property
    def last_sync(self) -> float:
        return self._config.get("last_sync", 0.0)

    def configure(self, method: str = "directory", target: str = "") -> None:
        self._config["enabled"] = True
        self._config["method"] = method
        self._config["target"] = target or str(self._sync_dir)
        self._save_config()
        Path(self._config["target"]).mkdir(parents=True, exist_ok=True)

    def disable(self) -> None:
        self._config["enabled"] = False
        self._save_config()

    def push(self) -> list[str]:
        """Push local settings to sync target. Returns list of synced files."""
        if not self.enabled:
            return []
        target = Path(self._config.get("target", self._sync_dir))
        target.mkdir(parents=True, exist_ok=True)
        synced = []
        for rel_path in SYNC_FILES:
            src = self._config_dir / rel_path
            dst = target / rel_path
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                synced.append(rel_path)
        self._config["last_sync"] = time.time()
        self._config["last_action"] = "push"
        self._save_config()
        return synced

    def pull(self) -> list[str]:
        """Pull settings from sync target to local. Returns list of synced files."""
        if not self.enabled:
            return []
        target = Path(self._config.get("target", self._sync_dir))
        synced = []
        for rel_path in SYNC_FILES:
            src = target / rel_path
            dst = self._config_dir / rel_path
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                # Backup before overwriting
                if dst.exists():
                    backup = dst.with_suffix(f".bak.{int(time.time())}")
                    shutil.copy2(dst, backup)
                shutil.copy2(src, dst)
                synced.append(rel_path)
        self._config["last_sync"] = time.time()
        self._config["last_action"] = "pull"
        self._save_config()
        return synced

    def diff(self) -> list[dict[str, str]]:
        """Compare local settings with sync target."""
        if not self.enabled:
            return []
        target = Path(self._config.get("target", self._sync_dir))
        diffs = []
        for rel_path in SYNC_FILES:
            local = self._config_dir / rel_path
            remote = target / rel_path
            local_exists = local.exists()
            remote_exists = remote.exists()
            if local_exists and remote_exists:
                if local.read_bytes() != remote.read_bytes():
                    diffs.append({"file": rel_path, "status": "modified"})
            elif local_exists and not remote_exists:
                diffs.append({"file": rel_path, "status": "local_only"})
            elif not local_exists and remote_exists:
                diffs.append({"file": rel_path, "status": "remote_only"})
        return diffs

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "method": self.sync_method,
            "target": self._config.get("target", ""),
            "last_sync": self.last_sync,
            "last_action": self._config.get("last_action", ""),
            "files_tracked": len(SYNC_FILES),
            "diffs": len(self.diff()) if self.enabled else 0,
        }


# Module singleton
_sync: SettingsSync | None = None


def get_settings_sync() -> SettingsSync:
    global _sync
    if _sync is None:
        _sync = SettingsSync()
    return _sync
