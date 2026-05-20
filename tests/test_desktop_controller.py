from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ccb.api.base import StreamEvent, ToolCall
from ccb.desktop_controller import (
    DesktopExecutionCancelled,
    DesktopSessionController,
    DesktopStreamEvent,
    drain_stream_queue,
    run_desktop_streaming_task,
)
from ccb.mcp_approval import ApprovalMode
from ccb.session import Session
from ccb.tools.base import ToolResult


def test_build_snapshot_aggregates_runtime_state() -> None:
    controller = DesktopSessionController(model="gpt-4o", cwd="/tmp/project", budget_tokens=1000)
    controller.session.add_user_message("hello")
    controller.session.add_assistant_message("world")
    controller.session.total_input_tokens = 120
    controller.session.total_output_tokens = 80
    controller.session.last_input_tokens = 200

    fake_cost = SimpleNamespace(total_cost_usd=1.25, last_turn_duration_ms=2500)

    with (
        patch("ccb.desktop_controller.get_active_account", return_value={"_name": "work"}),
        patch("ccb.desktop_controller.get_provider", return_value="openai"),
        patch("ccb.desktop_controller.get_context_limit", return_value=1000),
        patch("ccb.desktop_controller.get_cost_state", return_value=fake_cost),
        patch(
            "ccb.desktop_controller.event_summary",
            return_value={"total": 4, "last_problem": {"kind": "runtime", "action": "warn", "payload": {"error": "boom"}}},
        ),
        patch(
            "ccb.desktop_controller.get_job_manager",
            return_value=SimpleNamespace(summary=lambda: {"total": 3}, list_jobs=lambda: []),
        ),
        patch(
            "ccb.desktop_controller.get_permission_state",
            return_value={"effective_mode": "default", "workspace_rule_count": 2},
        ),
    ):
        snapshot = controller.build_snapshot()

    assert snapshot.account_name == "work"
    assert snapshot.provider == "openai"
    assert snapshot.context_percent == 20
    assert snapshot.budget_percent == 20
    assert snapshot.event_total == 4
    assert snapshot.job_total == 3
    assert snapshot.workspace_rule_count == 2
    assert "boom" in snapshot.last_problem


def test_list_sessions_uses_current_cwd_filter() -> None:
    controller = DesktopSessionController(model="gpt-4o", cwd="/tmp/project")

    with patch(
        "ccb.desktop_controller.list_persisted_sessions",
        return_value=[{"id": "s1", "cwd": "/tmp/project", "updated_at": 1, "model": "gpt-4o"}],
    ) as list_sessions:
        result = controller.list_sessions(limit=10)

    assert result[0]["id"] == "s1"
    list_sessions.assert_called_once_with(limit=10, cwd="/tmp/project")


def test_switch_session_replaces_active_session() -> None:
    controller = DesktopSessionController(model="gpt-4o", cwd="/tmp/project")
    loaded = Session(id="loaded", cwd="/tmp/other", model="claude-sonnet-4")

    with patch("ccb.desktop_controller.load_session", return_value=loaded):
        ok = controller.switch_session("loaded")

    assert ok is True
    assert controller.session.id == "loaded"
    assert controller.cwd == "/tmp/other"
    assert controller.model == "claude-sonnet-4"


def test_new_session_preserves_context() -> None:
    controller = DesktopSessionController(model="gpt-4o", cwd="/tmp/project")
    old_id = controller.session.id
    controller.session.add_user_message("hello")

    session = controller.new_session()

    assert session.id != old_id
    assert session.cwd == "/tmp/project"
    assert session.model == "gpt-4o"
    assert session.messages == []


def test_get_transcript_messages_filters_empty_entries() -> None:
    controller = DesktopSessionController(model="gpt-4o", cwd="/tmp/project")
    controller.session.add_user_message("hello")
    controller.session.add_assistant_message("")

    items = controller.get_transcript_messages()

    assert items == [("user", "hello")]


def test_get_tool_timeline_pairs_calls_and_results() -> None:
    controller = DesktopSessionController(model="gpt-4o", cwd="/tmp/project")
    tc = ToolCall(id="t1", name="bash", input={"command": "pwd"})
    controller.session.add_assistant_message("running", [tc])
    controller.session.add_tool_results([SimpleNamespace(tool_use_id="t1", content="/tmp/project", is_error=False)])

    items = controller.get_tool_timeline()

    assert items[0]["kind"] == "call"
    assert items[1]["kind"] == "result"
    assert items[1]["name"] == "bash"


def test_submit_updates_session_and_persists() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    fake_snapshot = SimpleNamespace(session_id=controller.session.id, model="test-model", cwd="/tmp/project")

    async def _execute_turn(_prompt, _callback):
        controller.session.add_user_message("hello")
        controller.session.add_assistant_message("hello back")
        return "hello back", fake_snapshot

    with patch.object(controller, "_execute_turn", _execute_turn):
        result = asyncio.run(controller.submit("hello"))

    assert result.response == "hello back"
    assert result.snapshot is fake_snapshot


def test_submit_rejects_blank_prompt() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")

    with pytest.raises(ValueError, match="Prompt cannot be empty"):
        asyncio.run(controller.submit("   "))


def test_stream_submit_emits_tool_and_done_events() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    fake_snapshot = SimpleNamespace(session_id=controller.session.id)
    seen: list[DesktopStreamEvent] = []

    async def _execute_turn(_prompt, on_event):
        on_event(DesktopStreamEvent(type="text", text="hel"))
        on_event(DesktopStreamEvent(type="tool_call", tool_name="bash", tool_input={"command": "pwd"}))
        on_event(DesktopStreamEvent(type="tool_result", tool_name="bash", tool_output="/tmp/project"))
        on_event(DesktopStreamEvent(type="done", snapshot=fake_snapshot))
        return "hello", fake_snapshot

    with patch.object(controller, "_execute_turn", _execute_turn):
        asyncio.run(controller.stream_submit("hello", seen.append))

    assert [event.type for event in seen] == ["text", "tool_call", "tool_result", "done"]
    assert seen[-1].snapshot is fake_snapshot


def test_execute_tool_calls_requests_permission_and_runs_tool() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    tool = SimpleNamespace(
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        execute=AsyncMock(return_value=ToolResult(output="ok", is_error=False)),
    )
    registry = SimpleNamespace(get=lambda _name: tool)
    events: list[DesktopStreamEvent] = []

    async def _run() -> None:
        with (
            patch("ccb.desktop_controller.needs_permission", return_value=True),
            patch("ccb.desktop_controller.is_auto_denied", return_value=False),
            patch.object(controller, "_request_permission", AsyncMock(return_value="allow_once")),
            patch("ccb.desktop_controller.record_approval"),
        ):
            result = await controller._execute_tool_calls(
                [ToolCall(id="t1", name="bash", input={"command": "pwd"})],
                registry,
                None,
                asyncio.Event(),
                events.append,
            )
            assert result[0].content == "ok"

    asyncio.run(_run())
    assert [event.type for event in events] == ["tool_result"]


def test_execute_tool_calls_routes_mcp_tools() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    registry = SimpleNamespace(get=lambda _name: None)
    mcp = SimpleNamespace(
        parse_mcp_tool_name=lambda name: ("demo", "search") if name == "mcp__demo__search" else None,
        call_tool=AsyncMock(return_value="mcp ok"),
    )
    approval_mgr = SimpleNamespace(check_approval=lambda *_args: (ApprovalMode.AUTO, "auto"))
    events: list[DesktopStreamEvent] = []

    async def _run() -> None:
        with patch("ccb.desktop_controller.get_approval_manager", return_value=approval_mgr):
            result = await controller._execute_tool_calls(
                [ToolCall(id="m1", name="mcp__demo__search", input={"q": "hi"})],
                registry,
                mcp,
                asyncio.Event(),
                events.append,
            )
            assert result[0].content == "mcp ok"

    asyncio.run(_run())
    assert [event.type for event in events] == ["tool_result"]


def test_execute_tool_calls_denies_mcp_tool_by_policy() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    registry = SimpleNamespace(get=lambda _name: None)
    mcp = SimpleNamespace(
        parse_mcp_tool_name=lambda name: ("demo", "search") if name == "mcp__demo__search" else None,
    )
    approval_mgr = SimpleNamespace(check_approval=lambda *_args: (ApprovalMode.DENY, "blocked"))
    events: list[DesktopStreamEvent] = []

    async def _run() -> None:
        with patch("ccb.desktop_controller.get_approval_manager", return_value=approval_mgr):
            result = await controller._execute_tool_calls(
                [ToolCall(id="m1", name="mcp__demo__search", input={"q": "hi"})],
                registry,
                mcp,
                asyncio.Event(),
                events.append,
            )
            assert result[0].is_error is True
            assert "blocked" in result[0].content

    asyncio.run(_run())
    assert [event.type for event in events] == ["tool_result"]
    assert events[0].is_error is True


def test_execute_tool_calls_asks_and_approves_mcp_tool() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    registry = SimpleNamespace(get=lambda _name: None)
    mcp = SimpleNamespace(
        parse_mcp_tool_name=lambda name: ("demo", "search") if name == "mcp__demo__search" else None,
        call_tool=AsyncMock(return_value="mcp ok"),
    )
    approval_mgr = SimpleNamespace(
        check_approval=lambda *_args: (ApprovalMode.ASK, "confirm"),
        record_approval=Mock(),
    )
    events: list[DesktopStreamEvent] = []

    async def _run() -> None:
        with (
            patch("ccb.desktop_controller.get_approval_manager", return_value=approval_mgr),
            patch.object(controller, "_request_permission", AsyncMock(return_value="allow_once")),
        ):
            result = await controller._execute_tool_calls(
                [ToolCall(id="m1", name="mcp__demo__search", input={"q": "hi"})],
                registry,
                mcp,
                asyncio.Event(),
                events.append,
            )
            assert result[0].content == "mcp ok"

    asyncio.run(_run())
    mcp.call_tool.assert_awaited_once_with("demo", "search", {"q": "hi"})
    approval_mgr.record_approval.assert_called_once_with("search", "demo", approved=True)


def test_execute_tool_calls_lists_mcp_resources() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    registry = SimpleNamespace(get=lambda _name: None)
    mcp = SimpleNamespace(
        parse_mcp_tool_name=lambda _name: None,
        list_resources=AsyncMock(return_value="res-a\nres-b"),
    )
    events: list[DesktopStreamEvent] = []

    async def _run() -> None:
        result = await controller._execute_tool_calls(
            [ToolCall(id="r1", name="list_mcp_resources", input={"server": "demo"})],
            registry,
            mcp,
            asyncio.Event(),
            events.append,
        )
        assert result[0].content == "res-a\nres-b"

    asyncio.run(_run())
    mcp.list_resources.assert_awaited_once_with("demo")
    assert [event.type for event in events] == ["tool_result"]


def test_execute_tool_calls_reads_mcp_resource() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    registry = SimpleNamespace(get=lambda _name: None)
    mcp = SimpleNamespace(
        parse_mcp_tool_name=lambda _name: None,
        read_resource=AsyncMock(return_value="resource body"),
    )
    events: list[DesktopStreamEvent] = []

    async def _run() -> None:
        result = await controller._execute_tool_calls(
            [ToolCall(id="r1", name="read_mcp_resource", input={"server": "demo", "uri": "memo://a"})],
            registry,
            mcp,
            asyncio.Event(),
            events.append,
        )
        assert result[0].content == "resource body"

    asyncio.run(_run())
    mcp.read_resource.assert_awaited_once_with("demo", "memo://a")
    assert [event.type for event in events] == ["tool_result"]


def test_request_permission_emits_event_and_accepts_approval() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    events: list[DesktopStreamEvent] = []

    async def _run() -> str:
        async def _approve_later() -> None:
            await asyncio.sleep(0)
            controller.approve_permission("p1", "allow_once")

        task = asyncio.create_task(
            controller._request_permission(
                ToolCall(id="p1", name="bash", input={"command": "pwd"}),
                events.append,
            )
        )
        await _approve_later()
        return await task

    choice = asyncio.run(_run())

    assert choice == "allow_once"
    assert events[0].type == "permission_request"


def test_execute_turn_streams_text_and_tool_calls() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    events: list[DesktopStreamEvent] = []
    stream_rounds = [
        [
            StreamEvent(type="text", text="hello "),
            StreamEvent(type="tool_use_end", tool_call=ToolCall(id="t1", name="bash", input={"command": "pwd"})),
            StreamEvent(type="done", usage={"input_tokens": 10, "output_tokens": 5}),
        ],
        [
            StreamEvent(type="text", text="done"),
            StreamEvent(type="done", usage={"input_tokens": 15, "output_tokens": 7}),
        ],
    ]

    async def _stream(**_kwargs):
        current = stream_rounds.pop(0)
        for event in current:
            yield event

    provider = SimpleNamespace(stream=_stream)
    registry = SimpleNamespace(
        all_schemas=lambda: [],
        get=lambda _name: SimpleNamespace(
            input_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            execute=AsyncMock(return_value=ToolResult(output="/tmp/project", is_error=False)),
        ),
    )

    async def _run() -> None:
        with (
            patch("ccb.desktop_controller.create_provider", return_value=provider),
            patch.object(controller, "_ensure_registry", return_value=registry),
            patch("ccb.desktop_controller.get_system_prompt", return_value="system"),
            patch("ccb.desktop_controller.save_session"),
            patch("ccb.desktop_controller.emit_event"),
            patch("ccb.desktop_controller.needs_permission", return_value=False),
        ):
            response, snapshot = await controller._execute_turn("hello", events.append)
            assert response == "done"
            assert snapshot.session_id == controller.session.id

    asyncio.run(_run())
    assert [event.type for event in events] == ["text", "tool_call", "tool_result", "text", "done"]


def test_drain_stream_queue_returns_pending_events() -> None:
    from queue import Queue

    queue: Queue[DesktopStreamEvent] = Queue()
    queue.put(DesktopStreamEvent(type="text", text="a"))
    queue.put(DesktopStreamEvent(type="done"))

    items = drain_stream_queue(queue)

    assert [item.type for item in items] == ["text", "done"]
    assert drain_stream_queue(queue) == []


def test_run_desktop_streaming_task_enqueues_events_once() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    seen_errors: list[Exception] = []

    async def _fake_stream_submit(_prompt, on_event):
        on_event(DesktopStreamEvent(type="text", text="hello"))
        on_event(DesktopStreamEvent(type="done"))

    with patch.object(controller, "stream_submit", _fake_stream_submit):
        thread, queue = run_desktop_streaming_task(
            controller,
            "hello",
            on_error=seen_errors.append,
        )
        thread.join(timeout=2)

    items = drain_stream_queue(queue)
    assert [item.type for item in items] == ["text", "done"]
    assert seen_errors == []


def test_close_disconnects_mcp_manager() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    manager = SimpleNamespace(disconnect_all=AsyncMock())
    controller._mcp_manager = manager

    asyncio.run(controller.close())

    manager.disconnect_all.assert_awaited_once()
    assert controller._mcp_manager is None


def test_run_desktop_streaming_task_reports_cancel_as_error_callback() -> None:
    controller = DesktopSessionController(model="test-model", cwd="/tmp/project")
    seen_errors: list[Exception] = []

    async def _fake_stream_submit(_prompt, _on_event):
        raise DesktopExecutionCancelled("Generation stopped")

    with patch.object(controller, "stream_submit", _fake_stream_submit):
        thread, _queue = run_desktop_streaming_task(
            controller,
            "hello",
            on_error=seen_errors.append,
        )
        thread.join(timeout=2)

    assert len(seen_errors) == 1
    assert isinstance(seen_errors[0], DesktopExecutionCancelled)
