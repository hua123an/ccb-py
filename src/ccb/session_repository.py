"""Session repository helpers for persisted + active session views."""
from __future__ import annotations
from pathlib import Path
from typing import Any

from ccb.session import Session


def save_session(session: Session) -> Path:
    """Persist a session to the standard sessions directory."""
    return session.save()


def load_session(session_id: str) -> Session | None:
    """Load a persisted session by id."""
    return Session.load(session_id)


def list_persisted_sessions(limit: int = 20, cwd: str | None = None) -> list[dict[str, Any]]:
    """List persisted sessions, optionally filtered by cwd."""
    return Session.list_sessions(limit=limit, cwd=cwd)


def list_sessions_with_active(
    *,
    persisted_limit: int = 20,
    cwd: str | None = None,
    active_sessions: dict[str, Session] | None = None,
    active_metadata: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """List persisted sessions merged with active in-memory session state."""
    sessions = list_persisted_sessions(limit=persisted_limit, cwd=cwd)
    known_ids = {session["id"] for session in sessions}

    if active_sessions:
        for sid, session in active_sessions.items():
            if sid not in known_ids:
                sessions.insert(0, {
                    "id": sid,
                    "cwd": session.cwd,
                    "model": session.model,
                    "updated_at": session.updated_at,
                    "messages": session.message_count or len(session.messages),
                })
                known_ids.add(sid)

    if active_metadata:
        for sid, metadata in active_metadata.items():
            if sid not in known_ids:
                sessions.insert(0, dict(metadata))
                known_ids.add(sid)

    return sessions


def load_serialized_session(
    session_id: str,
    *,
    active_sessions: dict[str, Session] | None = None,
) -> dict[str, Any] | None:
    """Load and serialize a session from active memory or persisted storage."""
    if active_sessions and session_id in active_sessions:
        return _serialize_session(active_sessions[session_id])

    session = load_session(session_id)
    if session is None:
        return None
    return _serialize_session(session)


def _serialize_message(message: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "role": message.role.value,
        "content": message.content,
    }
    if message.tool_calls:
        data["tool_calls"] = [
            {"id": tc.id, "name": tc.name, "input": tc.input}
            for tc in message.tool_calls
        ]
    if message.tool_results:
        data["tool_results"] = [
            {
                "tool_use_id": tr.tool_use_id,
                "content": tr.content,
                "is_error": tr.is_error,
            }
            for tr in message.tool_results
        ]
    return data


def _serialize_session(session: Session) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "cwd": session.cwd,
        "model": session.model,
        "total_input_tokens": session.total_input_tokens,
        "total_output_tokens": session.total_output_tokens,
        "last_input_tokens": session.last_input_tokens,
        "messages": [_serialize_message(message) for message in session.messages],
    }
