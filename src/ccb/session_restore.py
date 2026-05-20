"""Session cross-IDE restoration for seamless switching between IDE connections.

Allows saving and restoring session state when switching between IDEs
(VS Code, Zed, Cursor, etc.) and supports simultaneous multi-IDE access
to the same session.

Session states are stored in ``~/.ccb/acp_sessions/``.
"""
from __future__ import annotations

import json
import logging
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ccb.config import claude_dir
from ccb.json_store import read_json, write_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class IDEConnection:
    """Tracks a single IDE connection to a session."""
    ide_type: str          # "vscode", "zed", "cursor", etc.
    connection_id: str     # unique id for this connection instance
    connected_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    active: bool = True


@dataclass
class SessionState:
    """Full session state for cross-IDE save/restore."""
    session_id: str
    ide_type: str                           # last IDE that wrote state
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_state: dict[str, Any] = field(default_factory=dict)
    cursor_position: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    cwd: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "ide_type": self.ide_type,
            "messages": self.messages,
            "tool_state": self.tool_state,
            "cursor_position": self.cursor_position,
            "model": self.model,
            "cwd": self.cwd,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState:
        return cls(
            session_id=data.get("session_id", ""),
            ide_type=data.get("ide_type", ""),
            messages=data.get("messages", []),
            tool_state=data.get("tool_state", {}),
            cursor_position=data.get("cursor_position", {}),
            model=data.get("model", ""),
            cwd=data.get("cwd", ""),
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
            metadata=data.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# IDE format translators
# ---------------------------------------------------------------------------

class IDEFormatTranslator:
    """Translates session state between different IDE message formats.

    Each IDE has slightly different expectations for how messages, tool
    calls, and cursor state are represented.  This class handles the
    conversion so that a session started in VS Code can be resumed in Zed
    without manual intervention.
    """

    # Mapping of IDE names to their canonical format keys
    _IDE_FORMATS: dict[str, dict[str, Any]] = {
        "vscode": {
            "message_format": "openai",       # VS Code extensions typically use OpenAI format
            "tool_call_key": "tool_calls",
            "tool_result_key": "tool",
            "cursor_format": "position",      # {"line": N, "column": N}
        },
        "zed": {
            "message_format": "anthropic",    # Zed uses Anthropic native format
            "tool_call_key": "content",
            "tool_result_key": "tool_result",
            "cursor_format": "point",         # {"row": N, "col": N}
        },
        "cursor": {
            "message_format": "openai",
            "tool_call_key": "tool_calls",
            "tool_result_key": "tool",
            "cursor_format": "position",
        },
        "unknown": {
            "message_format": "openai",
            "tool_call_key": "tool_calls",
            "tool_result_key": "tool",
            "cursor_format": "position",
        },
    }

    @classmethod
    def get_format(cls, ide_type: str) -> dict[str, Any]:
        return cls._IDE_FORMATS.get(ide_type, cls._IDE_FORMATS["unknown"])

    @classmethod
    def translate_state(
        cls,
        source_ide: str,
        target_ide: str,
        state: SessionState,
    ) -> SessionState:
        """Convert session state from *source_ide* format to *target_ide* format.

        Returns a new ``SessionState`` with translated messages, tool state,
        and cursor position.  The original is not modified.
        """
        if source_ide == target_ide:
            # Return a copy so callers can mutate safely
            return SessionState.from_dict(state.to_dict())

        src_fmt = cls.get_format(source_ide)
        tgt_fmt = cls.get_format(target_ide)

        # Deep-copy so we don't mutate the original
        translated = SessionState.from_dict(state.to_dict())
        translated.ide_type = target_ide
        translated.updated_at = time.time()

        # Translate messages
        if src_fmt["message_format"] != tgt_fmt["message_format"]:
            translated.messages = cls._translate_messages(
                state.messages, src_fmt, tgt_fmt
            )

        # Translate cursor position
        translated.cursor_position = cls._translate_cursor(
            state.cursor_position, src_fmt, tgt_fmt
        )

        return translated

    @staticmethod
    def _translate_messages(
        messages: list[dict[str, Any]],
        src_fmt: dict[str, Any],
        tgt_fmt: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Translate message format between OpenAI and Anthropic conventions."""
        translated: list[dict[str, Any]] = []
        for msg in messages:
            new_msg = dict(msg)
            role = msg.get("role", "")

            # Handle tool calls in assistant messages
            if role == "assistant" and "tool_calls" in msg:
                if tgt_fmt["message_format"] == "anthropic":
                    # OpenAI -> Anthropic: move tool_calls into content blocks
                    content_blocks: list[dict[str, Any]] = []
                    if msg.get("content"):
                        content_blocks.append({"type": "text", "text": msg["content"]})
                    for tc in msg["tool_calls"]:
                        func = tc.get("function", {})
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "input": _safe_json_loads(func.get("arguments", "{}")),
                        })
                    new_msg["content"] = content_blocks
                    del new_msg["tool_calls"]
            elif role == "assistant" and isinstance(msg.get("content"), list):
                # Check if content is Anthropic-style blocks with tool_use
                blocks = msg["content"]
                has_tool_use = any(
                    b.get("type") == "tool_use" for b in blocks if isinstance(b, dict)
                )
                if has_tool_use and tgt_fmt["message_format"] == "openai":
                    # Anthropic -> OpenAI: extract tool_use blocks into tool_calls
                    text_parts: list[str] = []
                    tool_calls: list[dict[str, Any]] = []
                    for block in blocks:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            })
                    new_msg["content"] = "\n".join(text_parts) if text_parts else None
                    if tool_calls:
                        new_msg["tool_calls"] = tool_calls

            # Handle tool results in user messages
            if role == "user" and isinstance(msg.get("content"), list):
                blocks = msg["content"]
                has_tool_result = any(
                    b.get("type") == "tool_result" for b in blocks if isinstance(b, dict)
                )
                if has_tool_result and tgt_fmt["message_format"] == "openai":
                    # Anthropic tool_result -> OpenAI tool messages
                    non_tool = [b for b in blocks if isinstance(b, dict) and b.get("type") != "tool_result"]
                    tool_results_out: list[dict[str, Any]] = []
                    for b in blocks:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            tool_results_out.append({
                                "tool_use_id": b.get("tool_use_id", ""),
                                "content": b.get("content", ""),
                                "is_error": b.get("is_error", False),
                            })
                    if non_tool:
                        new_msg["content"] = non_tool
                    new_msg["tool_results"] = tool_results_out

            translated.append(new_msg)
        return translated

    @staticmethod
    def _translate_cursor(
        cursor: dict[str, Any],
        src_fmt: dict[str, Any],
        tgt_fmt: dict[str, Any],
    ) -> dict[str, Any]:
        """Translate cursor position between IDE formats."""
        if src_fmt["cursor_format"] == tgt_fmt["cursor_format"]:
            return dict(cursor)

        if src_fmt["cursor_format"] == "position" and tgt_fmt["cursor_format"] == "point":
            # {"line", "column"} -> {"row", "col"}
            return {
                "row": cursor.get("line", 0),
                "col": cursor.get("column", 0),
                "file": cursor.get("file", ""),
            }
        if src_fmt["cursor_format"] == "point" and tgt_fmt["cursor_format"] == "position":
            # {"row", "col"} -> {"line", "column"}
            return {
                "line": cursor.get("row", 0),
                "column": cursor.get("col", 0),
                "file": cursor.get("file", ""),
            }
        return dict(cursor)


def _safe_json_loads(s: str) -> Any:
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# Session Restorer
# ---------------------------------------------------------------------------

class SessionRestorer:
    """Manages saving/restoring session state across IDE connections.

    Features:
    - Auto-save state on IDE disconnect, auto-restore on reconnect.
    - Track active connections: session_id -> [IDEConnection, ...].
    - Translate state between IDE formats for seamless switching.
    - Supports simultaneous multi-IDE access to the same session.
    - Persists to ``~/.ccb/acp_sessions/``.
    """

    def __init__(self, storage_dir: Path | None = None) -> None:
        self._storage_dir = storage_dir or (claude_dir() / "acp_sessions")
        self._lock = threading.Lock()
        # In-memory state
        self._active_connections: dict[str, list[IDEConnection]] = {}
        self._session_states: dict[str, SessionState] = {}

    # -- Storage helpers --

    def _ensure_storage_dir(self) -> Path:
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        return self._storage_dir

    def _state_path(self, session_id: str) -> Path:
        return self._ensure_storage_dir() / f"{session_id}.json"

    # -- Save / Load --

    def save_session_state(
        self,
        session_id: str,
        ide_type: str,
        messages: list[dict[str, Any]],
        tool_state: dict[str, Any] | None = None,
        cursor_position: dict[str, Any] | None = None,
        model: str = "",
        cwd: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SessionState:
        """Save (or update) the state for a session.

        If a state already exists, it is merged (messages and tool_state
        are replaced; metadata is merged).
        """
        with self._lock:
            existing = self._session_states.get(session_id)
            now = time.time()

            state = SessionState(
                session_id=session_id,
                ide_type=ide_type,
                messages=messages,
                tool_state=tool_state or {},
                cursor_position=cursor_position or {},
                model=model or (existing.model if existing else ""),
                cwd=cwd or (existing.cwd if existing else ""),
                created_at=existing.created_at if existing else now,
                updated_at=now,
                metadata={**(existing.metadata if existing else {}), **(metadata or {})},
            )
            self._session_states[session_id] = state
            self._persist_state(state)
            return state

    def restore_session(
        self, session_id: str, ide_type: str = ""
    ) -> SessionState | None:
        """Restore a session state.

        If *ide_type* is specified and differs from the stored IDE, the
        state is automatically translated to the target IDE format.
        """
        with self._lock:
            state = self._session_states.get(session_id)
            if state is None:
                state = self._load_from_disk(session_id)
                if state is None:
                    return None
                self._session_states[session_id] = state

            if ide_type and ide_type != state.ide_type:
                state = IDEFormatTranslator.translate_state(
                    state.ide_type, ide_type, state
                )
            return state

    def translate_state(
        self, source_ide: str, target_ide: str, state: SessionState
    ) -> SessionState:
        """Convenience wrapper around ``IDEFormatTranslator.translate_state``."""
        return IDEFormatTranslator.translate_state(source_ide, target_ide, state)

    # -- Connection tracking --

    def register_connection(
        self, session_id: str, ide_type: str, connection_id: str
    ) -> IDEConnection:
        """Register a new IDE connection to a session."""
        conn = IDEConnection(
            ide_type=ide_type,
            connection_id=connection_id,
        )
        with self._lock:
            conns = self._active_connections.setdefault(session_id, [])
            conns.append(conn)
        return conn

    def unregister_connection(self, session_id: str, connection_id: str) -> bool:
        """Remove a connection.  Auto-saves state if this was the last connection."""
        with self._lock:
            conns = self._active_connections.get(session_id, [])
            for i, c in enumerate(conns):
                if c.connection_id == connection_id:
                    c.active = False
                    conns.pop(i)
                    # Auto-save if no more active connections for this session
                    if not conns:
                        self._active_connections.pop(session_id, None)
                        state = self._session_states.get(session_id)
                        if state:
                            self._persist_state(state)
                    return True
            return False

    def get_active_connections(self, session_id: str) -> list[IDEConnection]:
        """Return active connections for a session."""
        with self._lock:
            return [c for c in self._active_connections.get(session_id, []) if c.active]

    def get_connection_map(self) -> dict[str, list[IDEConnection]]:
        """Return the full session_id -> connections mapping."""
        with self._lock:
            return {
                sid: [c for c in conns if c.active]
                for sid, conns in self._active_connections.items()
            }

    def heartbeat(self, session_id: str, connection_id: str) -> bool:
        """Update the heartbeat timestamp for a connection."""
        with self._lock:
            for c in self._active_connections.get(session_id, []):
                if c.connection_id == connection_id:
                    c.last_heartbeat = time.time()
                    return True
            return False

    # -- Multi-IDE access --

    def list_active_sessions(self) -> list[dict[str, Any]]:
        """List all sessions that have at least one active connection."""
        with self._lock:
            result = []
            for sid, conns in self._active_connections.items():
                active = [c for c in conns if c.active]
                if active:
                    state = self._session_states.get(sid)
                    result.append({
                        "session_id": sid,
                        "connected_ides": [c.ide_type for c in active],
                        "updated_at": state.updated_at if state else 0,
                    })
            return result

    def disconnect_all(self, session_id: str) -> int:
        """Disconnect all IDEs from a session.  Auto-saves state.  Returns count."""
        with self._lock:
            conns = self._active_connections.pop(session_id, [])
            count = len(conns)
            if count:
                state = self._session_states.get(session_id)
                if state:
                    self._persist_state(state)
            return count

    # -- Persistence --

    def _persist_state(self, state: SessionState) -> None:
        """Write state to disk."""
        path = self._state_path(state.session_id)
        try:
            write_json(path, state.to_dict(), ensure_ascii=False)
        except OSError:
            logger.exception("Failed to persist session state for %s", state.session_id)

    def _load_from_disk(self, session_id: str) -> SessionState | None:
        """Load state from disk."""
        path = self._state_path(session_id)
        data = read_json(path)
        if not isinstance(data, dict):
            return None
        try:
            return SessionState.from_dict(data)
        except (KeyError, TypeError):
            return None

    def list_stored_sessions(self) -> list[dict[str, Any]]:
        """List all sessions persisted on disk."""
        storage = self._ensure_storage_dir()
        sessions: list[dict[str, Any]] = []
        for f in sorted(storage.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            data = read_json(f)
            if not isinstance(data, dict):
                continue
            sessions.append({
                "session_id": data.get("session_id", f.stem),
                "ide_type": data.get("ide_type", ""),
                "updated_at": data.get("updated_at", 0),
            })
        return sessions

    def delete_stored_session(self, session_id: str) -> bool:
        """Delete a persisted session state from disk."""
        path = self._state_path(session_id)
        if path.exists():
            path.unlink()
            with self._lock:
                self._session_states.pop(session_id, None)
            return True
        return False

    # -- Auto-save on disconnect (convenience) --

    def on_ide_disconnect(
        self,
        session_id: str,
        connection_id: str,
        current_messages: list[dict[str, Any]],
        tool_state: dict[str, Any] | None = None,
        cursor_position: dict[str, Any] | None = None,
    ) -> bool:
        """Called when an IDE disconnects.  Saves current state and removes the connection.

        Returns True if the session had an active connection that was removed.
        """
        # Find the connection's IDE type before removing
        ide_type = "unknown"
        with self._lock:
            for c in self._active_connections.get(session_id, []):
                if c.connection_id == connection_id:
                    ide_type = c.ide_type
                    break

        self.save_session_state(
            session_id=session_id,
            ide_type=ide_type,
            messages=current_messages,
            tool_state=tool_state,
            cursor_position=cursor_position,
        )
        return self.unregister_connection(session_id, connection_id)

    def on_ide_reconnect(
        self,
        session_id: str,
        ide_type: str,
        connection_id: str,
    ) -> SessionState | None:
        """Called when an IDE reconnects.  Registers connection and restores state.

        Returns the restored session state, or None if the session is unknown.
        """
        self.register_connection(session_id, ide_type, connection_id)
        return self.restore_session(session_id, ide_type)
