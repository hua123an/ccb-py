"""Tests for ACP protocol and session cross-IDE restoration."""
from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from ccb.acp_protocol import (
    ACPClient,
    ACPClientError,
    ACPError,
    ACPServer,
    ACPSessionState,
    ClientCapabilities,
    ClientInfo,
    InMemoryTransport,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    ServerCapabilities,
    StdioTransport,
    error_response,
)
from ccb.session_restore import (
    IDEFormatTranslator,
    SessionRestorer,
    SessionState,
)


# =========================================================================
# JSON-RPC types
# =========================================================================

class TestJSONRPCRequest:
    def test_to_dict_basic(self):
        req = JSONRPCRequest(method="initialize", params={"x": 1}, id=1)
        d = req.to_dict()
        assert d["jsonrpc"] == "2.0"
        assert d["method"] == "initialize"
        assert d["params"] == {"x": 1}
        assert d["id"] == 1

    def test_to_dict_no_params(self):
        req = JSONRPCRequest(method="ping", id="abc")
        d = req.to_dict()
        assert "params" not in d

    def test_to_dict_notification(self):
        """Notifications have no id."""
        req = JSONRPCRequest(method="notify")
        d = req.to_dict()
        assert "id" not in d


class TestJSONRPCResponse:
    def test_success(self):
        resp = JSONRPCResponse(id=1, result={"ok": True})
        d = resp.to_dict()
        assert d["jsonrpc"] == "2.0"
        assert d["id"] == 1
        assert d["result"] == {"ok": True}
        assert "error" not in d

    def test_error(self):
        resp = JSONRPCResponse(id=2, error={"code": -32600, "message": "bad"})
        d = resp.to_dict()
        assert d["error"]["code"] == -32600
        assert "result" not in d

    def test_to_json(self):
        resp = JSONRPCResponse(id=3, result="hello")
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["result"] == "hello"


class TestJSONRPCNotification:
    def test_to_dict(self):
        notif = JSONRPCNotification(method="acp/session_start", params={"sid": "123"})
        d = notif.to_dict()
        assert d["method"] == "acp/session_start"
        assert d["params"]["sid"] == "123"
        assert "id" not in d

    def test_to_json(self):
        notif = JSONRPCNotification(method="test")
        parsed = json.loads(notif.to_json())
        assert parsed["method"] == "test"


class TestErrorResponse:
    def test_basic(self):
        resp = error_response(1, ACPError.METHOD_NOT_FOUND, "no such method")
        d = resp.to_dict()
        assert d["id"] == 1
        assert d["error"]["code"] == -32601
        assert d["error"]["message"] == "no such method"

    def test_with_data(self):
        resp = error_response(None, ACPError.PARSE_ERROR, "bad json", data={"line": 5})
        d = resp.to_dict()
        assert d["error"]["data"] == {"line": 5}


# =========================================================================
# ACP Server
# =========================================================================

class TestACPServerDispatch:
    """Test the server's request dispatch without real transport I/O."""

    @pytest.fixture
    def server(self):
        return ACPServer()

    @pytest.mark.asyncio
    async def test_initialize(self, server):
        raw = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "client_info": {"name": "test-client", "version": "0.1", "ide_type": "vscode"},
                "capabilities": {"supports_streaming": True},
            },
        })
        resp_json = await server._dispatch(raw)
        resp = json.loads(resp_json)
        assert resp["id"] == 1
        result = resp["result"]
        assert result["server_info"]["name"] == "ccb-py"
        assert result["capabilities"]["tools"] is True
        assert result["capabilities"]["protocol_version"] == "1.0.0"
        assert server._initialized is True
        assert server._client_info.name == "test-client"
        assert server._client_info.ide_type == "vscode"

    @pytest.mark.asyncio
    async def test_method_before_init(self, server):
        raw = json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "session/list", "params": {},
        })
        resp_json = await server._dispatch(raw)
        resp = json.loads(resp_json)
        assert resp["error"]["code"] == ACPError.INVALID_REQUEST
        assert "not initialized" in resp["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_unknown_method(self, server):
        # Initialize first
        await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"client_info": {"name": "t", "version": "v"}},
        }))
        raw = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "foo/bar", "params": {}})
        resp_json = await server._dispatch(raw)
        resp = json.loads(resp_json)
        assert resp["error"]["code"] == ACPError.METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_parse_error(self, server):
        resp_json = await server._dispatch("not json{{{")
        resp = json.loads(resp_json)
        assert resp["error"]["code"] == ACPError.PARSE_ERROR

    @pytest.mark.asyncio
    async def test_missing_method(self, server):
        resp_json = await server._dispatch(json.dumps({"jsonrpc": "2.0", "id": 5}))
        resp = json.loads(resp_json)
        assert resp["error"]["code"] == ACPError.INVALID_REQUEST


class TestACPServerSessions:
    """Test session lifecycle through the server dispatch layer."""

    @pytest_asyncio.fixture
    async def initialized_server(self):
        server = ACPServer()
        await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"client_info": {"name": "t", "version": "v", "ide_type": "zed"}},
        }))
        return server

    @pytest.mark.asyncio
    async def test_session_create(self, initialized_server):
        server = initialized_server
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "session/create",
            "params": {"prompt": "hello", "tools": ["bash"], "cwd": "/tmp/project"},
        }))
        resp = json.loads(resp_json)
        sid = resp["result"]["session_id"]
        assert sid
        # Session should be tracked
        sess = server.get_session(sid)
        assert sess is not None
        assert sess.prompt == "hello"
        assert sess.cwd == "/tmp/project"
        assert "bash" in sess.tools
        assert "zed" in sess.connected_ides

    @pytest.mark.asyncio
    async def test_session_resume(self, initialized_server):
        server = initialized_server
        # Create
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "session/create",
            "params": {"prompt": "test"},
        }))
        sid = json.loads(resp_json)["result"]["session_id"]

        # Update session with some state
        server.update_session(sid, messages=[{"role": "user", "content": "hi"}])

        # Resume
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "session/resume",
            "params": {"session_id": sid},
        }))
        resp = json.loads(resp_json)
        assert resp["result"]["session_id"] == sid
        assert resp["result"]["cwd"] == "."
        assert len(resp["result"]["messages"]) == 1

    @pytest.mark.asyncio
    async def test_session_resume_not_found(self, initialized_server):
        server = initialized_server
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "session/resume",
            "params": {"session_id": "nonexistent"},
        }))
        resp = json.loads(resp_json)
        assert resp["result"]["error"] == "session_not_found"

    @pytest.mark.asyncio
    async def test_session_list(self, initialized_server):
        server = initialized_server
        # Create two sessions
        for i in range(2):
            await server._dispatch(json.dumps({
                "jsonrpc": "2.0", "id": 10 + i, "method": "session/create",
                "params": {"prompt": f"session {i}", "cwd": f"/tmp/p{i}"},
            }))
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 20, "method": "session/list", "params": {},
        }))
        resp = json.loads(resp_json)
        assert len(resp["result"]["sessions"]) == 2
        assert all("cwd" in session for session in resp["result"]["sessions"])

    @pytest.mark.asyncio
    async def test_session_remove(self, initialized_server):
        server = initialized_server
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "session/create",
            "params": {"prompt": "temp"},
        }))
        sid = json.loads(resp_json)["result"]["session_id"]
        assert server.remove_session(sid) is True
        assert server.get_session(sid) is None
        assert server.remove_session(sid) is False


class TestACPServerTools:
    """Test tool/execute and permission/check."""

    @pytest_asyncio.fixture
    async def initialized_server(self):
        server = ACPServer()
        await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"client_info": {"name": "t", "version": "v"}},
        }))
        return server

    @pytest.mark.asyncio
    async def test_tool_execute_default(self, initialized_server):
        server = initialized_server
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tool/execute",
            "params": {"name": "bash", "args": {"command": "ls"}, "session_id": ""},
        }))
        resp = json.loads(resp_json)
        assert resp["result"]["status"] == "not_implemented"
        assert resp["result"]["tool"] == "bash"

    @pytest.mark.asyncio
    async def test_tool_execute_custom_handler(self, initialized_server):
        server = initialized_server

        async def custom_execute(params):
            return {"output": f"ran {params.get('name', '?')}"}

        server.on("tool/execute", custom_execute)
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tool/execute",
            "params": {"name": "grep", "args": {"pattern": "foo"}},
        }))
        resp = json.loads(resp_json)
        assert resp["result"]["output"] == "ran grep"

    @pytest.mark.asyncio
    async def test_tool_execute_rejects_invalid_tool_args(self, initialized_server):
        server = initialized_server
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tool/execute",
            "params": {"name": "ask_user_question", "args": {"question": 123}},
        }))
        resp = json.loads(resp_json)
        assert resp["error"]["code"] == ACPError.INVALID_PARAMS
        assert "Invalid tool input:" in resp["error"]["message"]
        assert "Field 'question' must be a string, got int" in resp["error"]["message"]

    @pytest.mark.asyncio
    async def test_tool_execute_rejects_non_object_args(self, initialized_server):
        server = initialized_server
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tool/execute",
            "params": {"name": "ask_user_question", "args": "bad"},
        }))
        resp = json.loads(resp_json)
        assert resp["error"]["code"] == ACPError.INVALID_PARAMS
        assert "Tool args must be an object" in resp["error"]["message"]

    @pytest.mark.asyncio
    async def test_tool_execute_uses_session_cwd_for_registry(self, initialized_server, monkeypatch):
        server = initialized_server
        await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "session/create",
            "params": {"prompt": "hello", "cwd": "/tmp/acp-project"},
        }))
        session_id = next(iter(server._sessions))
        seen = {}

        class _FakeTool:
            input_schema = {"type": "object", "properties": {}}

        def _fake_create_default_registry(cwd):
            seen["cwd"] = cwd
            registry = type("R", (), {})()
            registry.get = lambda name: _FakeTool() if name == "ask_user_question" else None
            return registry

        monkeypatch.setattr("ccb.acp_protocol.create_default_registry", _fake_create_default_registry)

        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "tool/execute",
            "params": {"name": "ask_user_question", "args": {"question": "ok"}, "session_id": session_id},
        }))

        resp = json.loads(resp_json)
        assert resp["result"]["status"] == "not_implemented"
        assert seen["cwd"] == "/tmp/acp-project"

    @pytest.mark.asyncio
    async def test_permission_check(self, initialized_server):
        server = initialized_server
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "permission/check",
            "params": {"tool": "bash", "args": {"command": "rm -rf /"}},
        }))
        resp = json.loads(resp_json)
        assert resp["result"]["decision"] in ("allowed", "denied", "ask")

    @pytest.mark.asyncio
    async def test_permission_check_uses_session_cwd(self, initialized_server, monkeypatch):
        server = initialized_server
        await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "session/create",
            "params": {"prompt": "hello", "cwd": "/tmp/acp-project"},
        }))
        session_id = next(iter(server._sessions))
        seen = {}

        def _fake_needs_permission(tool, args, cwd=""):
            seen["tool"] = tool
            seen["args"] = args
            seen["cwd"] = cwd
            return False

        monkeypatch.setattr("ccb.permissions.needs_permission", _fake_needs_permission)

        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "permission/check",
            "params": {
                "tool": "bash",
                "args": {"command": "ls"},
                "session_id": session_id,
            },
        }))

        resp = json.loads(resp_json)
        assert resp["result"]["decision"] == "allowed"
        assert seen == {
            "tool": "bash",
            "args": {"command": "ls"},
            "cwd": "/tmp/acp-project",
        }

    @pytest.mark.asyncio
    async def test_skill_list(self, initialized_server):
        server = initialized_server
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "skill/list",
            "params": {"cwd": "."},
        }))
        resp = json.loads(resp_json)
        assert "skills" in resp["result"]

    @pytest.mark.asyncio
    async def test_skill_invoke(self, initialized_server):
        server = initialized_server
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "skill/invoke",
            "params": {"name": "test", "args": {}},
        }))
        resp = json.loads(resp_json)
        assert resp["error"]["code"] == ACPError.SKILL_NOT_FOUND

    @pytest.mark.asyncio
    async def test_skill_invoke_returns_prompt_for_known_skill(self, initialized_server, monkeypatch):
        server = initialized_server
        from ccb.skills import Skill

        monkeypatch.setattr(
            "ccb.skills.load_skills",
            lambda cwd: [
                Skill(
                    name="review",
                    description="Review code",
                    prompt="Review the current code",
                    source="bundled",
                    kind="skill",
                )
            ],
        )
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "skill/invoke",
            "params": {
                "name": "review",
                "args": {"cwd": ".", "prompt_args": {"focus": "security"}},
            },
        }))
        resp = json.loads(resp_json)
        assert resp["result"]["status"] == "ok"
        assert resp["result"]["skill"]["name"] == "review"
        assert "Review the current code" in resp["result"]["prompt"]
        assert '"focus": "security"' in resp["result"]["prompt"]


class TestACPServerUpdateSession:
    def test_update_messages(self):
        server = ACPServer()
        server._sessions["s1"] = ACPSessionState(session_id="s1")
        assert server.update_session("s1", messages=[{"role": "user", "content": "hi"}])
        assert len(server.get_session("s1").messages) == 1

    def test_update_nonexistent(self):
        server = ACPServer()
        assert server.update_session("nope", messages=[]) is False

    def test_update_tool_state(self):
        server = ACPServer()
        server._sessions["s2"] = ACPSessionState(session_id="s2")
        server.update_session("s2", tool_state={"bash": {"last_cmd": "ls"}})
        assert server.get_session("s2").tool_state["bash"]["last_cmd"] == "ls"

    def test_update_cursor(self):
        server = ACPServer()
        server._sessions["s3"] = ACPSessionState(session_id="s3")
        server.update_session("s3", cursor_position={"line": 10, "col": 5})
        assert server.get_session("s3").cursor_position["line"] == 10


# =========================================================================
# ACP Client
# =========================================================================

class TestACPClient:
    """Test the client methods using the in-memory transport pair."""

    @pytest_asyncio.fixture
    async def server_and_client(self):
        server_transport, client_transport = InMemoryTransport.pair()
        server = ACPServer(transport=server_transport)
        client = ACPClient(transport=client_transport)
        return server, client

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, server_and_client):
        server, client = server_and_client

        async def run_server():
            # Process up to 8 messages then stop
            for _ in range(8):
                line = await server.transport.receive()
                if line is None:
                    break
                resp = await server._dispatch(line)
                if resp is not None:
                    await server.transport.send(resp)
                    while not server._notification_queue.empty():
                        notif = server._notification_queue.get_nowait()
                        await server.transport.send(notif.to_json())

        server_task = asyncio.create_task(run_server())

        try:
            # Initialize
            result = await client.initialize(
                client_name="test", client_version="0.1", ide_type="cursor"
            )
            assert result["server_info"]["name"] == "ccb-py"
            assert client._initialized is True

            # Create session
            sid = await client.session_create(prompt="hello")
            assert sid

            # List sessions
            sessions = await client.session_list()
            assert len(sessions) == 1
            assert sessions[0]["session_id"] == sid

            # Resume session
            state = await client.session_resume(sid)
            assert state["session_id"] == sid

            # Tool execute
            result = await client.tool_execute("bash", {"command": "ls"}, sid)
            assert result["tool"] == "bash"

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_client_error(self, server_and_client):
        server, client = server_and_client

        async def run_server():
            for _ in range(2):
                line = await server.transport.receive()
                if line is None:
                    break
                resp = await server._dispatch(line)
                if resp is not None:
                    await server.transport.send(resp)

        server_task = asyncio.create_task(run_server())
        try:
            await client.initialize(client_name="t", client_version="v")
            with pytest.raises(ACPClientError) as exc_info:
                # Call a method that doesn't exist on the server
                await client._send_request("nonexistent/method", {})
            assert exc_info.value.code == ACPError.METHOD_NOT_FOUND
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass


class TestACPServerCustomHandlers:
    """Test overriding default handlers via on()."""

    @pytest.mark.asyncio
    async def test_custom_skill_invoke(self):
        server = ACPServer()
        # Initialize
        await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"client_info": {"name": "t", "version": "v"}},
        }))

        async def my_skill_handler(params):
            return {"result": f"invoked {params.get('name')}", "status": "ok"}

        server.on("skill/invoke", my_skill_handler)
        resp_json = await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "skill/invoke",
            "params": {"name": "review", "args": {"diff": True}},
        }))
        resp = json.loads(resp_json)
        assert resp["result"]["result"] == "invoked review"
        assert resp["result"]["status"] == "ok"


class TestACPServerNotifications:
    @pytest.mark.asyncio
    async def test_notifications_queued(self):
        server = ACPServer()
        await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"client_info": {"name": "t", "version": "v"}},
        }))
        await server._dispatch(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "session/create",
            "params": {"prompt": "test"},
        }))
        # The session_start notification should have been queued
        assert not server._notification_queue.empty()
        notif = server._notification_queue.get_nowait()
        assert notif.method == "acp/session_start"


# =========================================================================
# Transports
# =========================================================================

class TestStdioTransport:
    @pytest.mark.asyncio
    async def test_feed_and_receive(self):
        transport = StdioTransport()
        await transport.feed_line("hello")
        result = await transport.receive()
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_close(self):
        transport = StdioTransport()
        await transport.close()
        assert transport.is_connected is False
        result = await transport.receive()
        assert result is None


class TestInMemoryTransport:
    @pytest.mark.asyncio
    async def test_pair(self):
        server_t, client_t = InMemoryTransport.pair()
        await client_t.send("request")
        msg = await server_t.receive()
        assert msg == "request"

        await server_t.send("response")
        msg = await client_t.receive()
        assert msg == "response"

    @pytest.mark.asyncio
    async def test_close(self):
        t = InMemoryTransport()
        await t.close()
        assert t.is_connected is False
        assert await t.receive() is None


# =========================================================================
# Capability types
# =========================================================================

class TestCapabilities:
    def test_server_capabilities_defaults(self):
        caps = ServerCapabilities()
        assert caps.tools is True
        assert caps.skills is True
        assert caps.permissions is True
        assert "stdio" in caps.supported_transports
        assert "tcp" in caps.supported_transports
        assert caps.protocol_version == "1.0.0"

    def test_client_capabilities_defaults(self):
        caps = ClientCapabilities()
        assert caps.supports_streaming is False
        assert caps.supports_progress is False
        assert caps.ide_type == ""

    def test_client_info(self):
        info = ClientInfo(name="vscode-ext", version="2.0", ide_type="vscode")
        assert info.name == "vscode-ext"
        assert info.ide_type == "vscode"


# =========================================================================
# Session Restore: SessionState
# =========================================================================

class TestSessionState:
    def test_to_dict_roundtrip(self):
        state = SessionState(
            session_id="s1",
            ide_type="vscode",
            messages=[{"role": "user", "content": "hi"}],
            tool_state={"bash": {"last": "ls"}},
            cursor_position={"line": 5, "column": 10},
            model="claude-3",
            cwd="/tmp",
        )
        d = state.to_dict()
        restored = SessionState.from_dict(d)
        assert restored.session_id == "s1"
        assert restored.ide_type == "vscode"
        assert len(restored.messages) == 1
        assert restored.tool_state["bash"]["last"] == "ls"
        assert restored.cursor_position["line"] == 5
        assert restored.model == "claude-3"

    def test_from_dict_defaults(self):
        state = SessionState.from_dict({"session_id": "minimal"})
        assert state.session_id == "minimal"
        assert state.messages == []
        assert state.tool_state == {}
        assert state.ide_type == ""


# =========================================================================
# IDE Format Translator
# =========================================================================

class TestIDEFormatTranslator:
    def test_same_ide_noop(self):
        state = SessionState(session_id="s1", ide_type="vscode", messages=[{"role": "user", "content": "hi"}])
        result = IDEFormatTranslator.translate_state("vscode", "vscode", state)
        assert result.messages == state.messages
        assert result is not state  # Different object

    def test_vscode_to_zed_cursor(self):
        state = SessionState(
            session_id="s1",
            ide_type="vscode",
            cursor_position={"line": 10, "column": 20, "file": "main.py"},
        )
        result = IDEFormatTranslator.translate_state("vscode", "zed", state)
        assert result.cursor_position["row"] == 10
        assert result.cursor_position["col"] == 20
        assert result.ide_type == "zed"

    def test_zed_to_vscode_cursor(self):
        state = SessionState(
            session_id="s1",
            ide_type="zed",
            cursor_position={"row": 5, "col": 15},
        )
        result = IDEFormatTranslator.translate_state("zed", "vscode", state)
        assert result.cursor_position["line"] == 5
        assert result.cursor_position["column"] == 15

    def test_translate_openai_to_anthropic_messages(self):
        """OpenAI tool_calls format -> Anthropic content blocks."""
        state = SessionState(
            session_id="s1",
            ide_type="vscode",
            messages=[{
                "role": "assistant",
                "content": "Running command...",
                "tool_calls": [{
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                }],
            }],
        )
        result = IDEFormatTranslator.translate_state("vscode", "zed", state)
        msg = result.messages[0]
        # Should be Anthropic-style content blocks
        assert isinstance(msg.get("content"), list)
        blocks = msg["content"]
        tool_blocks = [b for b in blocks if b.get("type") == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0]["name"] == "bash"
        assert tool_blocks[0]["input"] == {"command": "ls"}
        assert "tool_calls" not in msg

    def test_translate_anthropic_to_openai_messages(self):
        """Anthropic content blocks -> OpenAI tool_calls format."""
        state = SessionState(
            session_id="s1",
            ide_type="zed",
            messages=[{
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Running..."},
                    {"type": "tool_use", "id": "tc1", "name": "grep", "input": {"pattern": "foo"}},
                ],
            }],
        )
        result = IDEFormatTranslator.translate_state("zed", "vscode", state)
        msg = result.messages[0]
        assert "tool_calls" in msg
        assert msg["tool_calls"][0]["function"]["name"] == "grep"
        assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"pattern": "foo"}
        assert msg["content"] == "Running..."

    def test_get_format_known(self):
        fmt = IDEFormatTranslator.get_format("vscode")
        assert fmt["message_format"] == "openai"

    def test_get_format_unknown(self):
        fmt = IDEFormatTranslator.get_format("some_new_ide")
        assert fmt["message_format"] == "openai"

    def test_translate_preserves_metadata(self):
        state = SessionState(
            session_id="s1",
            ide_type="vscode",
            messages=[],
            metadata={"key": "value"},
        )
        result = IDEFormatTranslator.translate_state("vscode", "zed", state)
        assert result.metadata == {"key": "value"}


# =========================================================================
# Session Restorer
# =========================================================================

class TestSessionRestorerPersistence:
    def test_save_and_restore(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        state = restorer.save_session_state(
            session_id="s1",
            ide_type="vscode",
            messages=[{"role": "user", "content": "hello"}],
            tool_state={"bash": {"last": "ls"}},
            cursor_position={"line": 1, "column": 0},
            model="claude-3",
            cwd="/tmp",
        )
        assert state.session_id == "s1"

        # File should exist on disk
        assert (tmp_path / "s1.json").exists()

        # Restore from disk (clear memory cache)
        restorer._session_states.clear()
        restored = restorer.restore_session("s1")
        assert restored is not None
        assert restored.session_id == "s1"
        assert len(restored.messages) == 1
        assert restored.model == "claude-3"

    def test_restore_with_translation(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.save_session_state(
            session_id="s2",
            ide_type="vscode",
            messages=[{"role": "user", "content": "hi"}],
            cursor_position={"line": 10, "column": 5},
        )
        restorer._session_states.clear()

        # Restore for a different IDE
        restored = restorer.restore_session("s2", ide_type="zed")
        assert restored is not None
        assert restored.ide_type == "zed"
        assert restored.cursor_position["row"] == 10
        assert restored.cursor_position["col"] == 5

    def test_restore_nonexistent(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        assert restorer.restore_session("nope") is None

    def test_delete_stored_session(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.save_session_state("s3", "vscode", messages=[])
        assert restorer.delete_stored_session("s3") is True
        assert restorer.restore_session("s3") is None
        assert restorer.delete_stored_session("s3") is False

    def test_list_stored_sessions(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.save_session_state("a", "vscode", messages=[])
        restorer.save_session_state("b", "zed", messages=[])
        sessions = restorer.list_stored_sessions()
        assert len(sessions) == 2
        ids = {s["session_id"] for s in sessions}
        assert ids == {"a", "b"}

    def test_save_merges_metadata(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.save_session_state("s1", "vscode", messages=[], metadata={"a": 1})
        restorer.save_session_state("s1", "vscode", messages=[], metadata={"b": 2})
        restored = restorer.restore_session("s1")
        assert restored.metadata == {"a": 1, "b": 2}

    def test_list_stored_sessions_skips_invalid_json(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        (tmp_path / "bad.json").write_text("not-json")

        sessions = restorer.list_stored_sessions()

        assert sessions == []


class TestSessionRestorerConnections:
    def test_register_and_list(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.register_connection("s1", "vscode", "conn-1")
        restorer.register_connection("s1", "zed", "conn-2")
        conns = restorer.get_active_connections("s1")
        assert len(conns) == 2
        assert {c.ide_type for c in conns} == {"vscode", "zed"}

    def test_unregister(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.register_connection("s1", "vscode", "c1")
        restorer.register_connection("s1", "zed", "c2")
        assert restorer.unregister_connection("s1", "c1") is True
        conns = restorer.get_active_connections("s1")
        assert len(conns) == 1
        assert conns[0].ide_type == "zed"

    def test_unregister_nonexistent(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        assert restorer.unregister_connection("s1", "nope") is False

    def test_unregister_last_auto_saves(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.save_session_state("s1", "vscode", messages=[{"role": "user", "content": "hi"}])
        restorer.register_connection("s1", "vscode", "c1")
        restorer.unregister_connection("s1", "c1")
        # State should be persisted
        assert (tmp_path / "s1.json").exists()

    def test_heartbeat(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.register_connection("s1", "vscode", "c1")
        assert restorer.heartbeat("s1", "c1") is True
        assert restorer.heartbeat("s1", "nonexistent") is False

    def test_get_connection_map(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.register_connection("s1", "vscode", "c1")
        restorer.register_connection("s2", "zed", "c2")
        m = restorer.get_connection_map()
        assert "s1" in m and "s2" in m

    def test_disconnect_all(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.save_session_state("s1", "vscode", messages=[])
        restorer.register_connection("s1", "vscode", "c1")
        restorer.register_connection("s1", "zed", "c2")
        count = restorer.disconnect_all("s1")
        assert count == 2
        assert restorer.get_active_connections("s1") == []

    def test_list_active_sessions(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.register_connection("s1", "vscode", "c1")
        restorer.register_connection("s2", "zed", "c2")
        active = restorer.list_active_sessions()
        assert len(active) == 2

    def test_list_active_after_disconnect(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.register_connection("s1", "vscode", "c1")
        restorer.unregister_connection("s1", "c1")
        active = restorer.list_active_sessions()
        assert len(active) == 0


class TestSessionRestorerAutoSaveRestore:
    def test_on_ide_disconnect(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.register_connection("s1", "vscode", "c1")
        result = restorer.on_ide_disconnect(
            "s1", "c1",
            current_messages=[{"role": "user", "content": "test"}],
            tool_state={"bash": {"last": "pwd"}},
            cursor_position={"line": 5, "column": 0},
        )
        assert result is True
        # State should be saved
        assert (tmp_path / "s1.json").exists()

    def test_on_ide_reconnect(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        # Save initial state from vscode
        restorer.save_session_state(
            "s1", "vscode",
            messages=[{"role": "user", "content": "hello"}],
            cursor_position={"line": 3, "column": 10},
        )
        restorer._session_states.clear()

        # Reconnect from zed
        state = restorer.on_ide_reconnect("s1", "zed", "c-new")
        assert state is not None
        assert state.ide_type == "zed"
        # Cursor should be translated
        assert state.cursor_position["row"] == 3
        assert state.cursor_position["col"] == 10
        # Connection should be registered
        conns = restorer.get_active_connections("s1")
        assert len(conns) == 1
        assert conns[0].ide_type == "zed"

    def test_multi_ide_simultaneous_access(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        restorer.save_session_state("s1", "vscode", messages=[{"role": "user", "content": "hi"}])

        # Two IDEs connect simultaneously
        restorer.register_connection("s1", "vscode", "vscode-1")
        restorer.register_connection("s1", "zed", "zed-1")

        conns = restorer.get_active_connections("s1")
        assert len(conns) == 2

        # Both can access the session
        state_vs = restorer.restore_session("s1", "vscode")
        state_zed = restorer.restore_session("s1", "zed")
        assert state_vs is not None
        assert state_zed is not None

        # Disconnect one, session still active
        restorer.unregister_connection("s1", "vscode-1")
        assert len(restorer.get_active_connections("s1")) == 1


class TestTranslateState:
    def test_convenience_method(self, tmp_path):
        restorer = SessionRestorer(storage_dir=tmp_path)
        state = SessionState(
            session_id="s1",
            ide_type="vscode",
            cursor_position={"line": 1, "column": 2},
        )
        result = restorer.translate_state("vscode", "zed", state)
        assert result.cursor_position["row"] == 1
        assert result.cursor_position["col"] == 2
