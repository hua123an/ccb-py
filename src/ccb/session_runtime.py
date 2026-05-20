"""Shared session lifecycle helpers for API-facing runtimes."""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Awaitable, Callable, MutableMapping

from ccb.api.base import Message, Role
from ccb.session import Session
from ccb.session_repository import load_session, save_session as persist_session


SessionCache = MutableMapping[str, Session]
SessionMetadataStore = MutableMapping[str, dict[str, Any]]
SessionLockStore = MutableMapping[str, asyncio.Lock]
SessionErrorLogger = Callable[[str, dict[str, Any] | None], None]


def append_message(session: Session, role: Role, content: str) -> None:
    """Append a plain-text message to a session."""
    if role == Role.USER:
        session.add_user_message(content)
        return
    session.add_assistant_message(content)


def serialize_message(message: Message) -> dict[str, Any]:
    """Convert a message to API-safe JSON."""
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


def serialize_session(session: Session) -> dict[str, Any]:
    """Convert a session to API-safe JSON."""
    return {
        "session_id": session.id,
        "cwd": session.cwd,
        "model": session.model,
        "total_input_tokens": session.total_input_tokens,
        "total_output_tokens": session.total_output_tokens,
        "last_input_tokens": session.last_input_tokens,
        "messages": [serialize_message(message) for message in session.messages],
    }


def remember_session(
    session: Session,
    metadata_store: SessionMetadataStore | None = None,
) -> dict[str, Any]:
    """Refresh active-session metadata from a session object."""
    existing = metadata_store.get(session.id, {}) if metadata_store is not None else {}
    meta = {
        "id": session.id,
        "model": session.model,
        "cwd": session.cwd,
        "created_at": existing.get("created_at", time.time()),
        "updated_at": session.updated_at,
        "messages": session.message_count or len(session.messages),
    }
    if metadata_store is not None:
        metadata_store[session.id] = meta
    return meta


def resolve_session(
    session_id: str | None = None,
    *,
    cwd: str | None = None,
    model: str | None = None,
    default_cwd: str | None = None,
    cache: SessionCache | None = None,
    metadata_store: SessionMetadataStore | None = None,
    create: bool = False,
) -> Session | None:
    """Load or create a session with consistent cwd/model precedence."""
    effective_cwd = default_cwd or os.getcwd()
    sid = (session_id or "").strip()
    metadata = metadata_store.get(sid, {}) if metadata_store is not None and sid else {}

    session: Session | None = None
    if sid and cache is not None and sid in cache:
        session = cache[sid]
    elif sid:
        try:
            session = load_session(sid)
        except Exception:
            session = None

    if session is None and not create:
        return None

    if session is None:
        session = Session(
            id=sid or str(uuid.uuid4())[:8],
            cwd=cwd or metadata.get("cwd") or effective_cwd,
            model=model or metadata.get("model") or "",
        )

    if cwd:
        session.cwd = cwd
    elif not session.cwd:
        session.cwd = metadata.get("cwd") or effective_cwd

    if model:
        session.model = model
    elif not session.model:
        session.model = metadata.get("model", "")

    if cache is not None:
        cache[session.id] = session
    if metadata_store is not None:
        remember_session(session, metadata_store)
    return session


def get_session_lock(
    lock_store: SessionLockStore,
    session_id: str | None,
) -> asyncio.Lock:
    """Return a stable async lock for a session id."""
    sid = (session_id or "").strip() or "__new__"
    lock = lock_store.get(sid)
    if lock is None:
        lock = asyncio.Lock()
        lock_store[sid] = lock
    return lock


def emit_runtime_warning(
    action: str,
    *,
    session_id: str = "",
    cwd: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Emit a runtime warning event for non-fatal operational failures."""
    try:
        from ccb.events import emit_event

        merged_payload = {"session_id": session_id, **(payload or {})}
        emit_event(
            "runtime",
            "session_runtime",
            action=action,
            payload=merged_payload,
            level="warning",
            cwd=cwd,
        )
    except Exception:
        pass


def remember_active_session(
    session: Session,
    metadata_store: SessionMetadataStore | None = None,
    *,
    max_entries: int = 200,
) -> dict[str, Any]:
    """Remember a session and evict stale metadata entries beyond the cap."""
    meta = remember_session(session, metadata_store)
    if metadata_store is None or len(metadata_store) <= max_entries:
        return meta

    removable = sorted(
        (
            (sid, info)
            for sid, info in metadata_store.items()
            if sid != session.id
        ),
        key=lambda item: item[1].get("updated_at", item[1].get("created_at", 0)),
    )
    overflow = len(metadata_store) - max_entries
    for sid, _info in removable[:overflow]:
        metadata_store.pop(sid, None)
    return meta


def prune_session_locks(
    lock_store: SessionLockStore,
    *,
    active_session_ids: set[str],
    max_entries: int = 200,
) -> None:
    """Drop idle session locks for sessions that are no longer active."""
    if len(lock_store) <= max_entries:
        return
    for sid in list(lock_store):
        if sid == "__new__":
            continue
        lock = lock_store[sid]
        if sid not in active_session_ids and not lock.locked():
            lock_store.pop(sid, None)
        if len(lock_store) <= max_entries:
            break


async def run_session_turn(
    prompt: str,
    *,
    session_id: str | None,
    cwd: str | None,
    model: str | None,
    default_cwd: str | None,
    run_query: Callable[..., Awaitable[str]],
    lock_store: SessionLockStore,
    cache: SessionCache | None = None,
    metadata_store: SessionMetadataStore | None = None,
    save_session: Callable[[Session], None] | None = None,
) -> tuple[Session, str]:
    """Run one serialized user->assistant session turn."""
    async with get_session_lock(lock_store, session_id):
        session = resolve_session(
            session_id,
            cwd=cwd,
            model=model,
            default_cwd=default_cwd or os.getcwd(),
            cache=cache,
            metadata_store=metadata_store,
            create=True,
        )
        assert session is not None
        append_message(session, Role.USER, prompt)
        result = await run_query(
            prompt,
            model=session.model or None,
            cwd=session.cwd or default_cwd or os.getcwd(),
            messages=[message for message in session.messages],
            session=session,
        )
        append_message(session, Role.ASSISTANT, result)
        if save_session is not None:
            save_session(session)
        else:
            persist_session(session)
        if metadata_store is not None:
            remember_active_session(session, metadata_store)
            prune_session_locks(lock_store, active_session_ids=set(metadata_store))
        elif cache is not None:
            prune_session_locks(lock_store, active_session_ids=set(cache))
        return session, result
