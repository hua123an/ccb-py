"""Session forking - branch a conversation into a new session.

Inspired by Anthropic Agent SDK's fork_session. Creates a new session
that preserves the message history up to a given point, allowing
experimentation without affecting the original conversation.
"""
from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any

from ccb.session import Message, Session
from ccb.api.base import Role
from ccb.json_store import read_json, write_json


def fork_session(
    session: Session,
    fork_point: int | None = None,
    label: str = "",
) -> Session:
    """Fork a session into a new independent session.

    Args:
        session: The source session to fork
        fork_point: Message index to fork from (None = fork all messages)
        label: Optional label for the forked session

    Returns:
        A new Session with copied messages
    """
    new_session = Session(cwd=session.cwd)

    # Copy messages up to fork_point
    messages = session.messages
    if fork_point is not None:
        messages = messages[:fork_point]

    for msg in messages:
        new_msg = Message(
            role=msg.role,
            content=msg.content,
            tool_calls=copy.deepcopy(msg.tool_calls) if msg.tool_calls else [],
            tool_results=copy.deepcopy(msg.tool_results) if msg.tool_results else [],
        )
        new_session.messages.append(new_msg)

    # Copy usage stats
    new_session.total_input_tokens = session.total_input_tokens
    new_session.total_output_tokens = session.total_output_tokens

    # Label unused
    # if label:
    #     new_session._label = label

    return new_session


def fork_at_last_assistant(session: Session) -> Session:
    """Fork at the last assistant message (retry from last response)."""
    for i in range(len(session.messages) - 1, -1, -1):
        from ccb.session import Role
        if session.messages[i].role == Role.ASSISTANT:
            return fork_session(session, fork_point=i, label="retry")
    return fork_session(session)


def fork_at_last_user(session: Session) -> Session:
    """Fork at the last user message (retry the last turn)."""
    for i in range(len(session.messages) - 1, -1, -1):
        from ccb.session import Role
        if session.messages[i].role == Role.USER:
            return fork_session(session, fork_point=i, label="branch")
    return fork_session(session)


def save_fork(
    original_session: Session,
    forked_session: Session,
    fork_name: str = "",
) -> Path:
    """Save a forked session to disk for later retrieval.

    Returns the path where the fork was saved.
    """
    session_dir = Path.home() / ".ccb" / "sessions" / "forks"
    session_dir.mkdir(parents=True, exist_ok=True)

    if not fork_name:
        fork_name = f"fork_{int(time.time())}"

    fork_path = session_dir / f"{fork_name}.json"

    data = {
        "name": fork_name,
        "created_at": time.time(),
        "source_messages": len(original_session.messages),
        "fork_messages": len(forked_session.messages),
        "cwd": forked_session.cwd,
        "messages": forked_session._to_dict()["messages"],
    }

    write_json(fork_path, data, ensure_ascii=False)
    return fork_path


def load_fork(fork_name: str) -> Session | None:
    """Load a forked session from disk."""
    fork_path = Path.home() / ".ccb" / "sessions" / "forks" / f"{fork_name}.json"
    data = read_json(fork_path)
    if not isinstance(data, dict):
        return None
    try:
        session = Session(cwd=data.get("cwd", "."))
        for msg_data in data.get("messages", []):
            msg = Message(
                role=Role(msg_data.get("role", "user")),
                content=msg_data.get("content", ""),
            )
            session.messages.append(msg)
        return session
    except (OSError, KeyError, ValueError):
        return None


def list_forks() -> list[dict[str, Any]]:
    """List all saved forks."""
    forks_dir = Path.home() / ".ccb" / "sessions" / "forks"
    if not forks_dir.exists():
        return []

    result = []
    for f in sorted(forks_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix == ".json":
            data = read_json(f)
            if not isinstance(data, dict):
                continue
            result.append({
                "name": data.get("name", f.stem),
                "created_at": data.get("created_at", 0),
                "source_messages": data.get("source_messages", 0),
                "fork_messages": data.get("fork_messages", 0),
                "path": str(f),
            })

    return result
