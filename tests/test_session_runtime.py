from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ccb.session import Session
from ccb.session_runtime import prune_session_locks, remember_active_session, run_session_turn


@pytest.mark.asyncio
async def test_run_session_turn_updates_metadata_and_saves_session():
    session_store: dict[str, Session] = {}
    metadata_store: dict[str, dict] = {}
    lock_store = {}
    save_session = MagicMock()
    run_query = AsyncMock(return_value="ok")

    session, result = await run_session_turn(
        "hello",
        session_id="s1",
        cwd="/tmp/project",
        model="test-model",
        default_cwd="/tmp/default",
        run_query=run_query,
        lock_store=lock_store,
        cache=session_store,
        metadata_store=metadata_store,
        save_session=save_session,
    )

    assert result == "ok"
    assert session.id == "s1"
    assert [m.content for m in session.messages] == ["hello", "ok"]
    save_session.assert_called_once_with(session)
    assert metadata_store["s1"]["cwd"] == "/tmp/project"
    assert metadata_store["s1"]["model"] == "test-model"


def test_remember_active_session_evicts_oldest_metadata():
    metadata_store = {
        "old-1": {"updated_at": 1, "created_at": 1},
        "old-2": {"updated_at": 2, "created_at": 2},
    }
    session = Session(id="new", cwd="/tmp/project", model="m")

    remember_active_session(session, metadata_store, max_entries=2)

    assert "new" in metadata_store
    assert len(metadata_store) == 2
    assert "old-1" not in metadata_store


def test_prune_session_locks_removes_idle_stale_locks():
    import asyncio

    lock_store = {
        "active": asyncio.Lock(),
        "stale": asyncio.Lock(),
        "__new__": asyncio.Lock(),
    }

    prune_session_locks(lock_store, active_session_ids={"active"}, max_entries=1)

    assert "active" in lock_store
    assert "__new__" in lock_store
    assert "stale" not in lock_store
