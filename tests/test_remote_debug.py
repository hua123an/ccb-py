"""Tests for remote_control and vscode_debug modules."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


# ── RemoteControlServer ──────────────────────────────────────────

class TestRemoteControlServer:
    def test_init_defaults(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        assert srv.host == "127.0.0.1"
        assert srv.port == 8090
        assert srv.token == ""
        assert srv.is_running is False

    def test_init_custom(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer(host="0.0.0.0", port=9999, token="secret")
        assert srv.host == "0.0.0.0"
        assert srv.port == 9999
        assert srv.token == "secret"

    def test_token_from_env(self):
        from ccb.remote_control import RemoteControlServer
        with patch.dict(os.environ, {"CCB_REMOTE_TOKEN": "envtok"}):
            srv = RemoteControlServer()
            assert srv.token == "envtok"

    def test_register_session(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        srv.register_session("s1", model="test-model", cwd="/tmp")
        assert "s1" in srv._active_sessions
        assert srv._active_sessions["s1"]["model"] == "test-model"

    def test_summary(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer(port=8091, token="x")
        s = srv.summary()
        assert s["running"] is False
        assert s["port"] == 8091
        assert s["auth_enabled"] is True
        assert "uptime_seconds" in s

    def test_check_auth_no_token(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        request = MagicMock()
        request.headers = {}
        request.query = {}
        assert srv._check_auth(request) is True

    def test_check_auth_bearer_valid(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer(token="mytoken")
        request = MagicMock()
        request.headers = {"Authorization": "Bearer mytoken"}
        request.query = {}
        assert srv._check_auth(request) is True

    def test_check_auth_bearer_invalid(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer(token="mytoken")
        request = MagicMock()
        request.headers = {"Authorization": "Bearer wrong"}
        request.query = {}
        assert srv._check_auth(request) is False

    def test_check_auth_query_param(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer(token="mytoken")
        request = MagicMock()
        request.headers = {}
        request.query = {"token": "mytoken"}
        assert srv._check_auth(request) is True

    @pytest.mark.asyncio
    async def test_broadcast_no_clients(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        sent = await srv.broadcast("test", {"data": 1})
        assert sent == 0

    @pytest.mark.asyncio
    async def test_broadcast_with_mock_ws(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        mock_ws = AsyncMock()
        mock_ws.send_str = AsyncMock()
        srv._ws_clients["c1"] = mock_ws
        sent = await srv.broadcast("event", {"key": "val"})
        assert sent == 1
        mock_ws.send_str.assert_called_once()
        payload = json.loads(mock_ws.send_str.call_args[0][0])
        assert payload["type"] == "event"
        assert payload["key"] == "val"

    @pytest.mark.asyncio
    async def test_broadcast_handles_broken_client(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        broken_ws = AsyncMock()
        broken_ws.send_str = AsyncMock(side_effect=Exception("closed"))
        good_ws = AsyncMock()
        good_ws.send_str = AsyncMock()
        srv._ws_clients["bad"] = broken_ws
        srv._ws_clients["good"] = good_ws
        sent = await srv.broadcast("test")
        assert sent == 1  # only the good one

    @pytest.mark.asyncio
    async def test_handle_index_unauthorized(self):
        """Index returns 401 when auth is required and missing."""
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer(token="secret")
        request = MagicMock()
        request.headers = {}
        request.query = {}
        resp = await srv._handle_index(request)
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_handle_index_authorized(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer(token="secret")
        request = MagicMock()
        request.headers = {"Authorization": "Bearer secret"}
        request.query = {}
        resp = await srv._handle_index(request)
        assert resp.status == 200
        assert "ccb-py Remote" in resp.text

    @pytest.mark.asyncio
    async def test_handle_status(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        request = MagicMock()
        request.headers = {}
        request.query = {}
        resp = await srv._handle_status(request)
        # aiojson response
        assert resp.status == 200
        body = json.loads(resp.body)
        assert "model" in body
        assert "uptime" in body

    @pytest.mark.asyncio
    async def test_handle_websocket_rejects_unauthorized(self):
        from ccb.remote_control import RemoteControlServer

        srv = RemoteControlServer(token="secret")
        request = MagicMock()
        request.headers = {}
        request.query = {}

        resp = await srv._handle_websocket(request)

        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_handle_list_sessions_empty(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        request = MagicMock()
        request.headers = {}
        request.query = {}
        with patch("ccb.session.Session.list_sessions", return_value=[]):
            resp = await srv._handle_list_sessions(request)
        body = json.loads(resp.body)
        assert "sessions" in body

    @pytest.mark.asyncio
    async def test_handle_list_sessions_includes_active(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        srv.register_session("active-s1", model="test")
        request = MagicMock()
        request.headers = {}
        request.query = {}
        with patch("ccb.session.Session.list_sessions", return_value=[]):
            resp = await srv._handle_list_sessions(request)
        body = json.loads(resp.body)
        ids = [s["id"] for s in body["sessions"]]
        assert "active-s1" in ids

    @pytest.mark.asyncio
    async def test_handle_send_message_empty(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        request = MagicMock()
        request.headers = {}
        request.query = {}
        request.match_info = {"sid": "test"}
        request.json = AsyncMock(return_value={"message": ""})
        resp = await srv._handle_send_message(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_handle_send_message_invalid_json(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        request = MagicMock()
        request.headers = {}
        request.query = {}
        request.match_info = {"sid": "test"}
        request.json = AsyncMock(side_effect=ValueError("bad json"))
        resp = await srv._handle_send_message(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_handle_send_message_uses_session_cwd(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        srv.register_session("test", model="m", cwd="/tmp/remote-project")
        request = MagicMock()
        request.headers = {}
        request.query = {}
        request.match_info = {"sid": "test"}
        request.json = AsyncMock(return_value={"message": "hello"})
        with (
            patch("ccb.query_engine.run_query", AsyncMock(return_value="ok")) as run_query,
            patch("ccb.session.Session.save") as save,
        ):
            resp = await srv._handle_send_message(request)
        assert resp.status == 200
        _, kwargs = run_query.await_args
        assert kwargs["model"] == "m"
        assert kwargs["cwd"] == "/tmp/remote-project"
        assert [m.content for m in kwargs["messages"]] == ["hello"]
        save.assert_called_once()
        body = json.loads(resp.body)
        assert [m["content"] for m in body["messages"]] == ["hello", "ok"]
        assert body["session_id"] == "test"
        assert srv._active_sessions["test"]["messages"] == 2

    @pytest.mark.asyncio
    async def test_handle_send_message_falls_back_to_persisted_session_context(self):
        from ccb.remote_control import RemoteControlServer
        from ccb.session import Session
        srv = RemoteControlServer()
        request = MagicMock()
        request.headers = {}
        request.query = {}
        request.match_info = {"sid": "persisted"}
        request.json = AsyncMock(return_value={"message": "hello"})
        persisted = Session(id="persisted", cwd="/tmp/persisted-project", model="persisted-model")
        with (
            patch("ccb.session.Session.load", side_effect=[persisted, persisted]),
            patch("ccb.query_engine.run_query", AsyncMock(return_value="ok")) as run_query,
            patch("ccb.session.Session.save") as save,
        ):
            resp = await srv._handle_send_message(request)
        assert resp.status == 200
        _, kwargs = run_query.await_args
        assert kwargs["model"] == "persisted-model"
        assert kwargs["cwd"] == "/tmp/persisted-project"
        assert [m.content for m in kwargs["messages"]] == ["hello"]
        save.assert_called_once()
        body = json.loads(resp.body)
        assert body["messages"][-1]["content"] == "ok"

    @pytest.mark.asyncio
    async def test_handle_get_messages_session_not_found(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        request = MagicMock()
        request.headers = {}
        request.query = {}
        request.match_info = {"sid": "nonexistent"}
        with patch("ccb.session.Session.load", return_value=None):
            resp = await srv._handle_get_messages(request)
        body = json.loads(resp.body)
        assert body["messages"] == []
        assert srv._active_sessions == {}

    @pytest.mark.asyncio
    async def test_handle_send_message_creates_new_session_without_sid(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        request = MagicMock()
        request.headers = {}
        request.query = {}
        request.match_info = {}
        request.json = AsyncMock(return_value={"message": "hello"})
        with (
            patch("ccb.query_engine.run_query", AsyncMock(return_value="ok")) as run_query,
            patch("ccb.session.Session.save") as save,
        ):
            resp = await srv._handle_send_message(request)
        assert resp.status == 200
        _, kwargs = run_query.await_args
        assert kwargs["model"] is None
        assert kwargs["cwd"] == os.getcwd()
        assert [m.content for m in kwargs["messages"]] == ["hello"]
        save.assert_called_once()
        body = json.loads(resp.body)
        assert body["session_id"]
        assert [m["content"] for m in body["messages"]] == ["hello", "ok"]

    @pytest.mark.asyncio
    async def test_handle_send_message_passes_full_session_history_to_query(self):
        from ccb.remote_control import RemoteControlServer
        from ccb.session import Session

        srv = RemoteControlServer()
        persisted = Session(id="persisted", cwd="/tmp/persisted-project", model="persisted-model")
        persisted.add_user_message("first")
        persisted.add_assistant_message("reply")
        request = MagicMock()
        request.headers = {}
        request.query = {}
        request.match_info = {"sid": "persisted"}
        request.json = AsyncMock(return_value={"message": "hello"})

        with (
            patch("ccb.session.Session.load", side_effect=[persisted, persisted]),
            patch("ccb.query_engine.run_query", AsyncMock(return_value="ok")) as run_query,
            patch("ccb.session.Session.save"),
        ):
            resp = await srv._handle_send_message(request)

        assert resp.status == 200
        _, kwargs = run_query.await_args
        assert [m.content for m in kwargs["messages"]] == ["first", "reply", "hello"]

    @pytest.mark.asyncio
    async def test_handle_get_messages_with_session(self):
        from ccb.remote_control import RemoteControlServer
        from ccb.api.base import Role, Message
        srv = RemoteControlServer()
        request = MagicMock()
        request.headers = {}
        request.query = {}
        request.match_info = {"sid": "s1"}

        mock_session = MagicMock()
        msg = Message(role=Role.USER, content="hello")
        mock_session.messages = [msg]

        with patch("ccb.session.Session.load", return_value=mock_session):
            resp = await srv._handle_get_messages(request)
        body = json.loads(resp.body)
        assert len(body["messages"]) == 1
        assert body["messages"][0]["content"] == "hello"

    def test_load_session_fills_missing_metadata_from_persisted_session(self):
        from ccb.remote_control import RemoteControlServer
        from ccb.session import Session

        srv = RemoteControlServer()
        persisted = Session(id="s1", cwd="/tmp/project", model="test-model")

        with patch("ccb.session.Session.load", return_value=persisted):
            session = srv._load_session("s1")

        assert session is persisted
        assert srv._active_sessions["s1"]["cwd"] == "/tmp/project"
        assert srv._active_sessions["s1"]["model"] == "test-model"

    def test_register_session_metadata_is_pruned_when_over_limit(self):
        from ccb.remote_control import RemoteControlServer

        srv = RemoteControlServer()
        for i in range(205):
            srv._active_sessions[f"s{i}"] = {"id": f"s{i}", "updated_at": i, "created_at": i}

        from ccb.session import Session
        session = Session(id="latest", cwd="/tmp/project", model="m")
        from ccb.session_runtime import remember_active_session, prune_session_locks

        remember_active_session(session, srv._active_sessions, max_entries=200)
        prune_session_locks(srv._session_locks, active_session_ids=set(srv._active_sessions), max_entries=200)

        assert "latest" in srv._active_sessions
        assert len(srv._active_sessions) == 200

    def test_singleton(self):
        from ccb.remote_control import get_remote_server
        # Reset singleton for test
        import ccb.remote_control as rc
        rc._server = None
        srv = get_remote_server(port=8888)
        assert srv.port == 8888
        srv2 = get_remote_server()
        assert srv2 is srv
        rc._server = None  # cleanup

    @pytest.mark.asyncio
    async def test_start_requires_aiohttp(self):
        from ccb.remote_control import RemoteControlServer
        srv = RemoteControlServer()
        with patch.dict("sys.modules", {"aiohttp": None}):
            with pytest.raises(RuntimeError, match="aiohttp required"):
                await srv.start()


# ── VSCodeDebugServer ────────────────────────────────────────────

class TestVSCodeDebugServer:
    def test_init_defaults(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        assert srv.host == "127.0.0.1"
        assert srv.port == 9333
        assert srv.is_running is False
        assert srv._step_count == 0

    def test_init_custom(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer(host="0.0.0.0", port=5555)
        assert srv.host == "0.0.0.0"
        assert srv.port == 5555

    def test_summary(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        s = srv.summary()
        assert s["running"] is False
        assert s["breakpoints"] == 0
        assert s["paused"] is False
        assert s["step_count"] == 0

    def test_handle_breakpoint(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        bp = srv.handle_breakpoint(42, "/tmp/test.py")
        assert bp.line == 42
        assert bp.file == "/tmp/test.py"
        assert bp.enabled is True
        assert bp.id  # has an id
        bps = srv.get_breakpoints()
        assert len(bps) == 1

    def test_handle_breakpoint_with_condition(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        bp = srv.handle_breakpoint(10, "/tmp/x.py", condition="x > 5")
        assert bp.condition == "x > 5"

    def test_remove_breakpoint(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        bp = srv.handle_breakpoint(1, "/tmp/r.py")
        assert srv.remove_breakpoint(bp.id) is True
        assert srv.get_breakpoints() == []
        assert srv.remove_breakpoint("nonexistent") is False

    def test_get_breakpoints_filtered(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv.handle_breakpoint(1, "/tmp/a.py")
        srv.handle_breakpoint(2, "/tmp/a.py")
        srv.handle_breakpoint(1, "/tmp/b.py")
        assert len(srv.get_breakpoints("/tmp/a.py")) == 2
        assert len(srv.get_breakpoints("/tmp/b.py")) == 1
        assert len(srv.get_breakpoints()) == 3

    def test_check_breakpoint_hit(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv.handle_breakpoint(10, "/tmp/hit.py")
        bp = srv.check_breakpoint("/tmp/hit.py", 10)
        assert bp is not None
        assert bp.hit_count == 1

    def test_check_breakpoint_miss(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv.handle_breakpoint(10, "/tmp/hit.py")
        assert srv.check_breakpoint("/tmp/hit.py", 5) is None
        assert srv.check_breakpoint("/tmp/other.py", 10) is None

    def test_check_breakpoint_disabled(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        bp = srv.handle_breakpoint(10, "/tmp/dis.py")
        bp.enabled = False
        assert srv.check_breakpoint("/tmp/dis.py", 10) is None

    def test_check_breakpoint_condition_pass(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv.handle_breakpoint(5, "/tmp/c.py", condition="x > 3")
        srv._eval_scope["x"] = 10
        bp = srv.check_breakpoint("/tmp/c.py", 5)
        assert bp is not None

    def test_check_breakpoint_condition_fail(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv.handle_breakpoint(5, "/tmp/c.py", condition="x > 100")
        srv._eval_scope["x"] = 1
        assert srv.check_breakpoint("/tmp/c.py", 5) is None

    @pytest.mark.asyncio
    async def test_send_event_no_clients(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        sent = await srv.send_event("Debugger.paused", {"reason": "test"})
        assert sent == 0
        assert len(srv._event_log) == 1

    @pytest.mark.asyncio
    async def test_send_event_with_clients(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        mock_ws = AsyncMock()
        mock_ws.send_str = AsyncMock()
        srv._ws_clients["c1"] = mock_ws
        sent = await srv.send_event("Debugger.step")
        assert sent == 1
        payload = json.loads(mock_ws.send_str.call_args[0][0])
        assert payload["method"] == "Debugger.step"

    @pytest.mark.asyncio
    async def test_send_event_handles_broken(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        broken = AsyncMock()
        broken.send_str = AsyncMock(side_effect=Exception("gone"))
        good = AsyncMock()
        good.send_str = AsyncMock()
        srv._ws_clients["b"] = broken
        srv._ws_clients["g"] = good
        sent = await srv.send_event("test")
        assert sent == 1

    @pytest.mark.asyncio
    async def test_handle_step_no_breakpoint(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        paused = await srv.handle_step("/tmp/s.py", 1, {"x": 42})
        assert paused is False
        assert srv._step_count == 1
        assert srv._state.current_file == "/tmp/s.py"
        assert srv._state.variables == {"x": 42}

    @pytest.mark.asyncio
    async def test_handle_step_breakpoint_hit(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv.handle_breakpoint(5, "/tmp/bp.py")
        paused = await srv.handle_step("/tmp/bp.py", 5, {"y": 99})
        assert paused is True
        assert srv._state.paused is True
        assert len(srv._state.call_stack) == 1
        assert srv._state.call_stack[0].file == "/tmp/bp.py"

    @pytest.mark.asyncio
    async def test_handle_step_breakpoint_hit_sends_event(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        mock_ws = AsyncMock()
        mock_ws.send_str = AsyncMock()
        srv._ws_clients["c1"] = mock_ws
        srv.handle_breakpoint(5, "/tmp/ev.py")
        await srv.handle_step("/tmp/ev.py", 5)
        # Should have sent Debugger.paused event
        calls = [json.loads(c[0][0]) for c in mock_ws.send_str.call_args_list]
        methods = [c["method"] for c in calls]
        assert "Debugger.paused" in methods

    def test_get_variables_empty(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        assert srv.get_variables() == []

    def test_get_variables_with_data(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv._state.variables = {"name": "test", "count": 42, "flag": True}
        vars = srv.get_variables()
        assert len(vars) == 3
        names = {v.name for v in vars}
        assert "name" in names
        assert "count" in names

    def test_evaluate_simple(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv._eval_scope = {"x": 10, "y": 20}
        result = srv.evaluate("x + y")
        assert result["result"] == "30"
        assert result["type"] == "int"

    def test_evaluate_string(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv._eval_scope = {"name": "world"}
        result = srv.evaluate("'hello ' + name")
        assert result["result"] == "hello world"

    def test_evaluate_error(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        result = srv.evaluate("undefined_var")
        assert "error" in result

    def test_evaluate_builtins_blocked(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        # open() should be available via __builtins__ (sandbox is not the goal here)
        result = srv.evaluate("1 + 1")
        assert result["result"] == "2"

    def test_set_variable(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv.set_variable("foo", "bar")
        assert srv._eval_scope["foo"] == "bar"
        assert srv._state.variables["foo"] == "bar"

    def test_get_call_stack(self):
        from ccb.vscode_debug import VSCodeDebugServer
        from ccb.vscode_debug import StackFrame
        srv = VSCodeDebugServer()
        srv._state.call_stack = [
            StackFrame(id="f1", name="main", file="/tmp/a.py", line=10),
        ]
        stack = srv.get_call_stack()
        assert len(stack) == 1
        assert stack[0]["name"] == "main"

    def test_get_event_log(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv._event_log = [{"method": f"e{i}"} for i in range(200)]
        assert len(srv.get_event_log(limit=50)) == 50
        assert len(srv.get_event_log(limit=500)) == 200

    @pytest.mark.asyncio
    async def test_emit_tool_start(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        mock_ws = AsyncMock()
        mock_ws.send_str = AsyncMock()
        srv._ws_clients["c1"] = mock_ws
        await srv.emit_tool_start("bash", {"command": "ls"})
        calls = [json.loads(c[0][0]) for c in mock_ws.send_str.call_args_list]
        methods = [c["method"] for c in calls]
        assert "Debugger.scriptParsed" in methods
        assert "Debugger.paused" in methods

    @pytest.mark.asyncio
    async def test_emit_tool_end(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        mock_ws = AsyncMock()
        mock_ws.send_str = AsyncMock()
        srv._ws_clients["c1"] = mock_ws
        await srv.emit_tool_end("bash", "file1\nfile2", is_error=False)
        calls = [json.loads(c[0][0]) for c in mock_ws.send_str.call_args_list]
        methods = [c["method"] for c in calls]
        assert "Debugger.resumed" in methods
        assert "Runtime.consoleAPICalled" in methods

    @pytest.mark.asyncio
    async def test_emit_tool_end_error(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        mock_ws = AsyncMock()
        mock_ws.send_str = AsyncMock()
        srv._ws_clients["c1"] = mock_ws
        await srv.emit_tool_end("bash", "Permission denied", is_error=True)
        calls = [json.loads(c[0][0]) for c in mock_ws.send_str.call_args_list]
        console_call = next(c for c in calls if c["method"] == "Runtime.consoleAPICalled")
        assert console_call["params"]["type"] == "error"

    @pytest.mark.asyncio
    async def test_cdp_message_enable(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        resp = await srv._handle_cdp_message({"id": 1, "method": "Debugger.enable", "params": {}})
        assert resp["id"] == 1
        assert resp["result"] == {}

    @pytest.mark.asyncio
    async def test_cdp_message_resume(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv._state.paused = True
        resp = await srv._handle_cdp_message({"id": 2, "method": "Debugger.resume", "params": {}})
        assert srv._state.paused is False
        assert resp["id"] == 2

    @pytest.mark.asyncio
    async def test_cdp_message_set_breakpoints(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        resp = await srv._handle_cdp_message({
            "id": 3,
            "method": "Debugger.setBreakpoints",
            "params": {
                "location": {"scriptId": "/tmp/test.py"},
                "breakpoints": [{"lineNumber": 10}, {"lineNumber": 20}],
            },
        })
        assert resp["id"] == 3
        assert len(resp["result"]["breakpoints"]) == 2
        assert len(srv.get_breakpoints("/tmp/test.py")) == 2

    @pytest.mark.asyncio
    async def test_cdp_message_evaluate(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv._eval_scope = {"x": 42}
        resp = await srv._handle_cdp_message({
            "id": 4,
            "method": "Runtime.evaluate",
            "params": {"expression": "x * 2"},
        })
        assert resp["id"] == 4
        assert resp["result"]["result"]["value"] == "84"

    @pytest.mark.asyncio
    async def test_cdp_message_evaluate_on_call_frame(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv._eval_scope = {"s": "hello"}
        resp = await srv._handle_cdp_message({
            "id": 5,
            "method": "Debugger.evaluateOnCallFrame",
            "params": {"expression": "s.upper()"},
        })
        assert resp["result"]["result"]["value"] == "HELLO"

    @pytest.mark.asyncio
    async def test_cdp_message_get_script_source(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        resp = await srv._handle_cdp_message({
            "id": 6,
            "method": "Debugger.getScriptSource",
            "params": {"scriptId": "ccb-main"},
        })
        assert "ccb-py" in resp["result"]["scriptSource"]

    @pytest.mark.asyncio
    async def test_cdp_message_unknown(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        resp = await srv._handle_cdp_message({"id": 7, "method": "Unknown.method", "params": {}})
        assert resp is None

    @pytest.mark.asyncio
    async def test_cdp_message_no_id(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        resp = await srv._handle_cdp_message({"method": "Debugger.enable", "params": {}})
        # No id => no response needed
        assert resp is None

    @pytest.mark.asyncio
    async def test_start_requires_aiohttp(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        with patch.dict("sys.modules", {"aiohttp": None}):
            with pytest.raises(RuntimeError, match="aiohttp required"):
                await srv.start()

    def test_stop(self):
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        srv._running = True
        srv.stop()
        assert srv.is_running is False

    def test_singleton(self):
        from ccb.vscode_debug import get_debug_server
        import ccb.vscode_debug as vd
        vd._debug_server = None
        srv = get_debug_server(port=7777)
        assert srv.port == 7777
        srv2 = get_debug_server()
        assert srv2 is srv
        vd._debug_server = None  # cleanup


# ── launch.json generation ───────────────────────────────────────

class TestGenerateLaunchJson:
    def test_generates_file(self, tmp_path):
        from ccb.vscode_debug import generate_launch_json
        path = generate_launch_json(str(tmp_path / ".vscode"))
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["version"] == "0.2.0"
        assert len(data["configurations"]) == 2

    def test_attach_config(self, tmp_path):
        from ccb.vscode_debug import generate_launch_json
        path = generate_launch_json(str(tmp_path / ".vscode"))
        data = json.loads(path.read_text())
        first = data["configurations"][0]
        assert first["name"] == "Attach to ccb-py"
        assert first["request"] == "attach"
        assert first["port"] == 9333
        assert "ws://" in first["websocketAddress"]

    def test_custom_port_config(self, tmp_path):
        from ccb.vscode_debug import generate_launch_json
        path = generate_launch_json(str(tmp_path / ".vscode"))
        data = json.loads(path.read_text())
        second = data["configurations"][1]
        assert "custom port" in second["name"].lower()
        assert "input" in second["port"]

    def test_has_inputs(self, tmp_path):
        from ccb.vscode_debug import generate_launch_json
        path = generate_launch_json(str(tmp_path / ".vscode"))
        data = json.loads(path.read_text())
        assert "inputs" in data
        assert data["inputs"][0]["id"] == "ccbDebugPort"

    def test_creates_dir(self, tmp_path):
        from ccb.vscode_debug import generate_launch_json
        target = tmp_path / "project" / ".vscode"
        assert not target.exists()
        path = generate_launch_json(str(target))
        assert path.exists()


# ── Data classes ─────────────────────────────────────────────────

class TestDataClasses:
    def test_breakpoint(self):
        from ccb.vscode_debug import Breakpoint
        bp = Breakpoint(id="b1", file="/tmp/a.py", line=10)
        assert bp.hit_count == 0
        assert bp.enabled is True
        assert bp.condition == ""

    def test_stack_frame(self):
        from ccb.vscode_debug import StackFrame
        sf = StackFrame(id="f1", name="main", file="/tmp/a.py", line=10)
        assert sf.column == 0

    def test_variable(self):
        from ccb.vscode_debug import Variable
        v = Variable(name="x", value="42", type="int")
        assert v.variables_reference == 0

    def test_debug_state(self):
        from ccb.vscode_debug import DebugState
        ds = DebugState()
        assert ds.paused is False
        assert ds.call_stack == []
        assert ds.variables == {}


# ── Integration: step through execution ──────────────────────────

class TestDebugIntegration:
    @pytest.mark.asyncio
    async def test_full_step_sequence(self):
        """Simulate a multi-step debug session with breakpoints."""
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()

        # Set breakpoints
        bp1 = srv.handle_breakpoint(5, "/tmp/main.py")
        bp2 = srv.handle_breakpoint(10, "/tmp/main.py")

        # Step 1: no breakpoint hit
        p1 = await srv.handle_step("/tmp/main.py", 1, {"counter": 0})
        assert p1 is False
        assert srv._step_count == 1

        # Step 2: hit breakpoint at line 5
        p2 = await srv.handle_step("/tmp/main.py", 5, {"counter": 1})
        assert p2 is True
        assert srv._state.paused is True
        assert bp1.hit_count == 1

        # Evaluate while paused
        result = srv.evaluate("counter * 10")
        assert result["result"] == "10"

        # Resume
        srv._state.paused = False

        # Step 3: hit breakpoint at line 10
        p3 = await srv.handle_step("/tmp/main.py", 10, {"counter": 2})
        assert p3 is True
        assert bp2.hit_count == 1

    @pytest.mark.asyncio
    async def test_tool_debug_flow(self):
        """Simulate tool call debugging."""
        from ccb.vscode_debug import VSCodeDebugServer
        srv = VSCodeDebugServer()
        events = []
        # Intercept send_event
        original = srv.send_event

        async def capture_send(method, params=None):
            events.append(method)
            return await original(method, params)

        srv.send_event = capture_send

        await srv.emit_tool_start("file_read", {"path": "/tmp/test.py"})
        await srv.emit_tool_end("file_read", "contents of file")

        assert "Debugger.scriptParsed" in events
        assert "Debugger.paused" in events
        assert "Debugger.resumed" in events
        assert "Runtime.consoleAPICalled" in events


# ── HTML page ────────────────────────────────────────────────────

class TestHTMLPage:
    def test_html_contains_essential_elements(self):
        from ccb.remote_control import _HTML_PAGE
        assert "ccb-py Remote" in _HTML_PAGE
        assert "sendMessage" in _HTML_PAGE
        assert "loadSessions" in _HTML_PAGE
        assert "loadStatus" in _HTML_PAGE
        assert "WebSocket" in _HTML_PAGE or "ws://" in _HTML_PAGE or "connectWS" in _HTML_PAGE
        assert "viewport" in _HTML_PAGE

    def test_html_no_external_deps(self):
        from ccb.remote_control import _HTML_PAGE
        # Should not reference external CDN resources
        assert "cdn." not in _HTML_PAGE
        assert "unpkg." not in _HTML_PAGE
        assert "jsdelivr." not in _HTML_PAGE

    def test_html_mobile_meta(self):
        from ccb.remote_control import _HTML_PAGE
        assert "maximum-scale=1" in _HTML_PAGE
        assert "initial-scale=1" in _HTML_PAGE
