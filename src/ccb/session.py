"""Session management - save, load, resume conversations."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ccb.api.base import Message, Role, ToolCall, ToolResult
from ccb.config import claude_dir


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[Message] = field(default_factory=list)
    cwd: str = ""
    model: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    # Snapshot of the MOST RECENT request's input_tokens. Unlike
    # total_input_tokens (cumulative across all tool-call rounds), this equals
    # the size of the conversation the model is currently seeing — which is
    # what "context usage" actually means.
    last_input_tokens: int = 0

    def add_user_message(
        self,
        text: str,
        images: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> None:
        self.messages.append(Message(
            role=Role.USER, content=text,
            images=images or [], files=files or [],
        ))
        self.updated_at = time.time()

    def add_assistant_message(self, text: str, tool_calls: list[ToolCall] | None = None) -> None:
        self.messages.append(Message(
            role=Role.ASSISTANT,
            content=text,
            tool_calls=tool_calls or [],
        ))
        self.updated_at = time.time()

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.messages.append(Message(role=Role.USER, tool_results=results))
        self.updated_at = time.time()

    def add_usage(self, usage: dict[str, int]) -> None:
        inp = usage.get("input_tokens", 0)
        self.total_input_tokens += inp
        self.total_output_tokens += usage.get("output_tokens", 0)
        if inp > 0:
            self.last_input_tokens = inp

    def save(self) -> Path:
        sessions_dir = claude_dir() / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        path = sessions_dir / f"{self.id}.json"
        path.write_text(json.dumps(self._to_dict(), indent=2, ensure_ascii=False))
        return path

    @classmethod
    def load(cls, session_id: str) -> Session | None:
        path = claude_dir() / "sessions" / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return cls._from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    @classmethod
    def list_sessions(cls, limit: int = 20, cwd: str | None = None) -> list[dict[str, Any]]:
        """List recent sessions, optionally filtered to a specific project directory.

        When *cwd* is provided, only sessions whose stored ``cwd`` matches
        (or is a subdirectory of) the given path are returned.  This keeps
        the session list scoped to the current project.
        """
        sessions_dir = claude_dir() / "sessions"
        if not sessions_dir.exists():
            return []
        # Normalise filter path once
        filter_cwd = cwd.rstrip("/") if cwd else None
        entries = []
        for f in sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if len(entries) >= limit:
                break
            try:
                data = json.loads(f.read_text())
                sess_cwd = data.get("cwd", "")
                # Filter by project directory
                if filter_cwd and sess_cwd:
                    norm = sess_cwd.rstrip("/")
                    if norm != filter_cwd and not norm.startswith(filter_cwd + "/"):
                        continue
                elif filter_cwd and not sess_cwd:
                    continue
                entries.append({
                    "id": data.get("id", f.stem),
                    "cwd": sess_cwd,
                    "model": data.get("model", ""),
                    "updated_at": data.get("updated_at", 0),
                    "messages": len(data.get("messages", [])),
                })
            except Exception:
                continue
        return entries

    def _to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cwd": self.cwd,
            "model": self.model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "last_input_tokens": self.last_input_tokens,
            "messages": [self._msg_to_dict(m) for m in self.messages],
        }

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> Session:
        s = cls(
            id=data["id"],
            cwd=data.get("cwd", ""),
            model=data.get("model", ""),
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
            total_input_tokens=data.get("total_input_tokens", 0),
            total_output_tokens=data.get("total_output_tokens", 0),
            last_input_tokens=data.get("last_input_tokens", 0),
        )
        for md in data.get("messages", []):
            s.messages.append(cls._msg_from_dict(md))
        return s

    @staticmethod
    def _msg_to_dict(m: Message) -> dict[str, Any]:
        d: dict[str, Any] = {"role": m.role.value, "content": m.content}
        if m.tool_calls:
            d["tool_calls"] = [{"id": tc.id, "name": tc.name, "input": tc.input} for tc in m.tool_calls]
        if m.tool_results:
            d["tool_results"] = [
                {"tool_use_id": tr.tool_use_id, "content": tr.content, "is_error": tr.is_error}
                for tr in m.tool_results
            ]
        if m.images:
            d["images"] = m.images
        if m.files:
            d["files"] = m.files
        return d

    @staticmethod
    def _msg_from_dict(d: dict[str, Any]) -> Message:
        m = Message(
            role=Role(d["role"]),
            content=d.get("content", ""),
            images=d.get("images", []),
            files=d.get("files", []),
        )
        for tc in d.get("tool_calls", []):
            m.tool_calls.append(ToolCall(id=tc["id"], name=tc["name"], input=tc["input"]))
        for tr in d.get("tool_results", []):
            m.tool_results.append(ToolResult(
                tool_use_id=tr["tool_use_id"], content=tr["content"], is_error=tr.get("is_error", False)
            ))
        return m
