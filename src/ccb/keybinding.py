"""Custom keybinding engine for ccb-py.

Supports loading user-defined keybindings from config and
resolving conflicts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class KeyBinding:
    keys: str  # e.g. "ctrl+k", "alt+enter", "escape,escape"
    action: str  # e.g. "submit", "newline", "cancel", "clear"
    mode: str = "all"  # "all", "normal", "insert", "command"
    description: str = ""
    enabled: bool = True


# Default keybindings
DEFAULT_BINDINGS: list[dict[str, str]] = [
    {"keys": "enter", "action": "submit", "description": "Submit input"},
    {"keys": "escape,enter", "action": "newline", "description": "Insert newline"},
    {"keys": "alt+enter", "action": "newline", "description": "Insert newline (alt)"},
    {"keys": "shift+enter", "action": "newline", "description": "Insert newline (shift)"},
    {"keys": "ctrl+c", "action": "cancel", "description": "Cancel / clear input"},
    {"keys": "ctrl+d", "action": "exit", "description": "Exit ccb"},
    {"keys": "ctrl+l", "action": "clear_screen", "description": "Clear screen"},
    {"keys": "ctrl+r", "action": "history_search", "description": "Search history"},
    {"keys": "ctrl+k", "action": "kill_line", "description": "Kill to end of line"},
    {"keys": "ctrl+u", "action": "kill_line_back", "description": "Kill to start of line"},
    {"keys": "ctrl+w", "action": "kill_word", "description": "Kill previous word"},
    {"keys": "tab", "action": "complete", "description": "Autocomplete"},
    {"keys": "up", "action": "history_prev", "description": "Previous history"},
    {"keys": "down", "action": "history_next", "description": "Next history"},
    {"keys": "ctrl+p", "action": "history_prev", "description": "Previous history (emacs)"},
    {"keys": "ctrl+n", "action": "history_next", "description": "Next history (emacs)"},
    {"keys": "ctrl+z", "action": "undo", "description": "Undo"},
    {"keys": "ctrl+y", "action": "redo", "description": "Redo / yank"},
]


class KeyBindingManager:
    """Manages keybindings with user customization."""

    def __init__(self) -> None:
        self._bindings: list[KeyBinding] = []
        self._user_overrides: dict[str, str] = {}
        self._load_defaults()

    def _load_defaults(self) -> None:
        for b in DEFAULT_BINDINGS:
            self._bindings.append(KeyBinding(
                keys=b["keys"],
                action=b["action"],
                description=b.get("description", ""),
            ))

    def load_user_config(self, path: Path | None = None) -> int:
        """Load user keybinding overrides from JSON. Returns count loaded."""
        path = path or (Path.home() / ".claude" / "keybindings.json")
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text())
            count = 0
            for entry in data if isinstance(data, list) else []:
                keys = entry.get("keys", "")
                action = entry.get("action", "")
                if keys and action:
                    self.bind(keys, action,
                              mode=entry.get("mode", "all"),
                              description=entry.get("description", ""))
                    count += 1
            return count
        except (json.JSONDecodeError, OSError):
            return 0

    def save_user_config(self, path: Path | None = None) -> None:
        path = path or (Path.home() / ".claude" / "keybindings.json")
        data = [
            {"keys": b.keys, "action": b.action, "mode": b.mode, "description": b.description}
            for b in self._bindings
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    def bind(self, keys: str, action: str, mode: str = "all", description: str = "") -> None:
        """Add or override a keybinding."""
        # Remove existing binding for same keys+mode
        self._bindings = [
            b for b in self._bindings
            if not (b.keys == keys and b.mode == mode)
        ]
        self._bindings.append(KeyBinding(
            keys=keys, action=action, mode=mode, description=description,
        ))

    def unbind(self, keys: str, mode: str = "all") -> bool:
        before = len(self._bindings)
        self._bindings = [
            b for b in self._bindings
            if not (b.keys == keys and b.mode == mode)
        ]
        return len(self._bindings) < before

    def get_action(self, keys: str, mode: str = "all") -> str | None:
        for b in reversed(self._bindings):
            if b.keys == keys and b.enabled:
                if b.mode == "all" or b.mode == mode:
                    return b.action
        return None

    def list_bindings(self, mode: str | None = None) -> list[KeyBinding]:
        if mode:
            return [b for b in self._bindings if b.mode in ("all", mode) and b.enabled]
        return [b for b in self._bindings if b.enabled]

    def find_conflicts(self) -> list[tuple[KeyBinding, KeyBinding]]:
        """Find keybinding conflicts."""
        conflicts = []
        seen: dict[str, KeyBinding] = {}
        for b in self._bindings:
            key = f"{b.keys}:{b.mode}"
            if key in seen:
                conflicts.append((seen[key], b))
            seen[key] = b
        return conflicts

    def reset_defaults(self) -> None:
        self._bindings.clear()
        self._load_defaults()


# Module singleton
_manager: KeyBindingManager | None = None


def get_keybinding_manager() -> KeyBindingManager:
    global _manager
    if _manager is None:
        _manager = KeyBindingManager()
        _manager.load_user_config()
    return _manager
