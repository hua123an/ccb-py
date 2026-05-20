# -*- coding: utf-8 -*-
"""
Tests for Phase 3: Swarm Soft-Interrupt Event Broker & Optimistic Conflict Resolution.
"""
import asyncio
import os
import tempfile
from pathlib import Path
import pytest

from ccb.swarm_broker import SoftInterruptBroker
from ccb.agent_context import set_current_session_id, current_session_id
from ccb.tools.file import FileReadTool, FileWriteTool, FileEditTool
from ccb.tools.base import ToolRegistry
from ccb.session import Session


@pytest.mark.asyncio
async def test_soft_interrupt_broker_pub_sub():
    broker = SoftInterruptBroker.get_instance()
    
    # Reset broker state
    broker._subscribers.clear()
    broker._pending_events.clear()

    session_a = "session_a"
    session_b = "session_b"
    file_path = "/mock/workspace/utils.py"

    # Subscribe session A and session B to same file
    broker.subscribe(file_path, session_a)
    broker.subscribe(file_path, session_b)

    # Session A publishes change
    await broker.publish_file_change(
        actor_id=session_a,
        file_path=file_path,
        diff_summary="Added helper() function."
    )

    # Session B should have pending event, but Session A should not
    events_a = broker.get_pending_events(session_a)
    events_b = broker.get_pending_events(session_b)

    assert len(events_a) == 0
    assert len(events_b) == 1
    assert events_b[0]["type"] == "file_touch"
    assert events_b[0]["actor"] == session_a
    assert "Added helper()" in events_b[0]["diff"]

    # Retrieval clears the events
    events_b_second = broker.get_pending_events(session_b)
    assert len(events_b_second) == 0

    # Unsubscribe session B
    broker.unsubscribe_session(session_b)
    await broker.publish_file_change(
        actor_id=session_a,
        file_path=file_path,
        diff_summary="Another change."
    )
    assert len(broker.get_pending_events(session_b)) == 0


@pytest.mark.asyncio
async def test_file_tools_pub_sub_integration():
    broker = SoftInterruptBroker.get_instance()
    broker._subscribers.clear()
    broker._pending_events.clear()

    session_a = "session_a"
    session_b = "session_b"

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "test.txt")
        
        # Session A reads/subscribes
        set_current_session_id(session_a)
        # Create the file first so FileReadTool can read it
        Path(test_file).write_text("line 1\nline 2\n")
        
        read_tool = FileReadTool()
        await read_tool.execute({"file_path": test_file}, cwd=tmpdir)

        # Session B also reads/subscribes
        set_current_session_id(session_b)
        await read_tool.execute({"file_path": test_file}, cwd=tmpdir)

        # Verify both subscribed to the resolved path of test_file
        resolved_path = str(Path(test_file).resolve())
        assert session_a in broker._subscribers[resolved_path]
        assert session_b in broker._subscribers[resolved_path]

        # Session A edits/writes the file
        set_current_session_id(session_a)
        write_tool = FileWriteTool()
        await write_tool.execute({"file_path": test_file, "content": "line 1\nline 2\nline 3\n"}, cwd=tmpdir)

        # Session B should have received the touch notification
        events_b = broker.get_pending_events(session_b)
        assert len(events_b) == 1
        assert events_b[0]["actor"] == session_a
        assert "Wrote/overwrote" in events_b[0]["diff"]

        # Session A performs an edit
        set_current_session_id(session_a)
        edit_tool = FileEditTool()
        await edit_tool.execute({
            "file_path": test_file,
            "old_string": "line 3",
            "new_string": "line 3 edited",
        }, cwd=tmpdir)

        # Session B should receive edit notification
        events_b_edit = broker.get_pending_events(session_b)
        assert len(events_b_edit) == 1
        assert "Edited file" in events_b_edit[0]["diff"]
