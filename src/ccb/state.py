"""Centralized application state management for ccb-py.

Single source of truth for runtime state — replaces scattered globals.
Observable: listeners can subscribe to state changes.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class AppState:
    """All mutable runtime state in one place."""

    # Session
    session_id: str = ""
    session_start: float = 0.0
    cwd: str = ""
    git_root: str = ""
    git_branch: str = ""

    # Model
    model: str = ""
    provider: str = ""
    api_base: str = ""

    # Modes
    vim_mode: bool = False
    sandbox_mode: bool = False
    multi_pass: bool = False
    plan_mode: bool = False
    fast_mode: bool = False
    effort: str = "normal"  # low, normal, high
    output_style: str = "normal"  # normal, minimal, structured

    # Conversation
    message_count: int = 0
    turn_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    context_window_usage: float = 0.0

    # Tools
    tools_called: int = 0
    files_read: int = 0
    files_written: int = 0
    commands_run: int = 0

    # Plugin
    plugins_loaded: int = 0
    plugin_commands_available: int = 0

    # UI
    theme: str = "monokai"
    color_scheme: str = ""

    # Status
    is_busy: bool = False
    last_error: str = ""
    last_activity: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


# Type for change listeners
ChangeListener = Callable[[str, Any, Any], None]  # (key, old_val, new_val)


class StateManager:
    """Observable state container."""

    def __init__(self) -> None:
        self._state = AppState()
        self._listeners: list[ChangeListener] = []
        self._key_listeners: dict[str, list[ChangeListener]] = {}
        self._history: list[tuple[float, str, Any, Any]] = []  # (time, key, old, new)

    @property
    def state(self) -> AppState:
        return self._state

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self._state, key, default)

    def set(self, key: str, value: Any) -> None:
        old = getattr(self._state, key, None)
        if old == value:
            return
        setattr(self._state, key, value)
        self._state.last_activity = time.time()
        self._history.append((time.time(), key, old, value))
        # Keep history bounded
        if len(self._history) > 500:
            self._history = self._history[-250:]
        # Notify listeners
        for fn in self._listeners:
            try:
                fn(key, old, value)
            except Exception:
                pass
        for fn in self._key_listeners.get(key, []):
            try:
                fn(key, old, value)
            except Exception:
                pass

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            self.set(k, v)

    def subscribe(self, listener: ChangeListener, key: str | None = None) -> Callable[[], None]:
        """Subscribe to state changes. Returns unsubscribe function."""
        if key:
            self._key_listeners.setdefault(key, []).append(listener)
            return lambda: self._key_listeners[key].remove(listener)
        else:
            self._listeners.append(listener)
            return lambda: self._listeners.remove(listener)

    def snapshot(self) -> dict[str, Any]:
        return self._state.to_dict()

    def recent_changes(self, count: int = 20) -> list[dict[str, Any]]:
        return [
            {"time": t, "key": k, "old": o, "new": n}
            for t, k, o, n in self._history[-count:]
        ]

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self._state.to_dict(), indent=2, default=str))

    def load(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for k, v in data.items():
                if hasattr(self._state, k):
                    setattr(self._state, k, v)
        except (json.JSONDecodeError, OSError):
            pass


# Module singleton
_manager: StateManager | None = None


def get_state() -> StateManager:
    global _manager
    if _manager is None:
        _manager = StateManager()
    return _manager
