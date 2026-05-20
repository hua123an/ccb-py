"""Tests for ccb-py data input interface.

Covers:
- IDEBridge message handling and input parsing
- ACP protocol request/response handling
- Session message input (user, assistant, tool results)
- Message serialization and deserialization
- Input validation for various message types
"""
import asyncio
import json

import pytest

from ccb.bridge import IDEBridge, BridgeMessage
from ccb.acp_protocol import (
    ACPError,
    ACPServer,
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCNotification,
    error_response,
)
from ccb.session import Session
from ccb.api.base import Message, Role, ToolCall, ToolResult


# ============================================================================
# IDEBridge Data Input Tests
# ============================================================================

class TestIDEBridgeInputParsing:
    """Test IDEBridge JSON message parsing and validation."""

    @pytest.mark.asyncio
    async def test_parse_valid_message(self):
        raw = json.dumps({"type": "request", "method": "ping", "id": "123"})
        data = json.loads(raw)
        msg = BridgeMessage(
            type=data.get("type", "request"),
            method=data.get("method", ""),
            id=data.get("id"),
            params=data.get("params", {}),
        )
        assert msg.type == "request"
        assert msg.method == "ping"
        assert msg.id == "123"

    @pytest.mark.asyncio
    async def test_parse_message_with_params(self):
        raw = json.dumps({
            "type": "request",
            "method": "openFile",
            "id": "456",
            "params": {"path": "/tmp/test.py", "line": 42},
        })
        data = json.loads(raw)
        msg = BridgeMessage(
            type=data.get("type", "request"),
            method=data.get("method", ""),
            id=data.get("id"),
            params=data.get("params", {}),
        )
        assert msg.params["path"] == "/tmp/test.py"
        assert msg.params["line"] == 42

    @pytest.mark.asyncio
    async def test_parse_notification_without_id(self):
        raw = json.dumps({
            "type": "notification",
            "method": "fileChanged",
            "params": {"path": "/tmp/foo.txt"},
        })
        data = json.loads(raw)
        msg = BridgeMessage(
            type=data.get("type", "notification"),
            method=data.get("method", ""),
            id=data.get("id"),
            params=data.get("params", {}),
        )
        assert msg.id is None
        assert msg.type == "notification"

    @pytest.mark.asyncio
    async def test_parse_response_message(self):
        raw = json.dumps({
            "type": "response",
            "id": "789",
            "result": {"status": "ok"},
        })
        data = json.loads(raw)
        assert data["type"] == "response"
        assert data["result"]["status"] == "ok"


class TestIDEBridgeInputHandlers:
    """Test IDEBridge handler processing with various inputs."""

    @pytest.mark.asyncio
    async def test_open_file_handler_input(self):
        bridge = IDEBridge()
        msg = BridgeMessage(
            type="request",
            method="openFile",
            params={"path": "/src/main.py", "line": 100},
        )
        result = await bridge._handle_open_file(msg)
        assert result["opened"] == "/src/main.py"
        assert result["line"] == 100

    @pytest.mark.asyncio
    async def test_execute_command_handler_input(self):
        bridge = IDEBridge()
        msg = BridgeMessage(
            type="request",
            method="executeCommand",
            params={"command": "vscode.diff", "args": ["a.txt", "b.txt"]},
        )
        result = await bridge._handle_execute_command(msg)
        assert result["executed"] == "vscode.diff"
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_get_diagnostics_handler(self):
        bridge = IDEBridge()
        msg = BridgeMessage(type="request", method="getDiagnostics")
        result = await bridge._handle_diagnostics(msg)
        assert "diagnostics" in result
        assert isinstance(result["diagnostics"], list)


# ============================================================================
# ACP Protocol Data Input Tests
# ============================================================================

class TestACPJSONRPCInput:
    """Test ACP JSON-RPC request parsing and input validation."""

    def test_parse_valid_request(self):
        raw = json.dumps({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"client_info": {"name": "test"}},
            "id": 1,
        })
        data = json.loads(raw)
        req = JSONRPCRequest(
            method=data["method"],
            params=data.get("params", {}),
            id=data.get("id"),
        )
        assert req.method == "initialize"
        assert req.params["client_info"]["name"] == "test"

    def test_parse_request_without_id(self):
        """Notifications have no id field."""
        raw = json.dumps({
            "jsonrpc": "2.0",
            "method": "session/start",
            "params": {"prompt": "Hello"},
        })
        data = json.loads(raw)
        req = JSONRPCRequest(
            method=data["method"],
            params=data.get("params", {}),
            id=data.get("id"),
        )
        assert req.id is None
        assert req.method == "session/start"

    def test_parse_notification(self):
        raw = json.dumps({
            "jsonrpc": "2.0",
            "method": "acp/session_start",
            "params": {"session_id": "abc123"},
        })
        data = json.loads(raw)
        notif = JSONRPCNotification(
            method=data["method"],
            params=data.get("params", {}),
        )
        assert notif.method == "acp/session_start"
        assert notif.params["session_id"] == "abc123"

    def test_roundtrip_request(self):
        req = JSONRPCRequest(
            method="tool/execute",
            params={"name": "bash", "args": {"command": "ls"}},
            id=42,
        )
        data = req.to_dict()
        assert data["method"] == "tool/execute"
        assert data["params"]["name"] == "bash"
        assert data["id"] == 42

    def test_roundtrip_response(self):
        resp = JSONRPCResponse(
            id=1,
            result={"session_id": "xyz789"},
        )
        data = resp.to_dict()
        assert data["result"]["session_id"] == "xyz789"
        json_str = resp.to_json()
        assert '"session_id"' in json_str


class TestACPInputValidation:
    """Test ACP input validation and error handling paths."""

    def test_invalid_json_input(self):
        """Invalid JSON should produce parse error."""
        import io
        from unittest.mock import patch

        server = ACPServer()
        raw = "not valid json{"
        with patch("sys.stdout", new=io.StringIO()):
            resp = asyncio.run(server._dispatch(raw))
        assert resp is not None
        data = json.loads(resp)
        assert data["error"]["code"] == ACPError.PARSE_ERROR

    def test_missing_method_input(self):
        server = ACPServer()
        raw = json.dumps({"jsonrpc": "2.0", "params": {}, "id": 1})
        resp = server._dispatch(raw)
        assert resp is not None
        data = json.loads(resp)
        assert data["error"]["code"] == ACPError.INVALID_REQUEST

    def test_unknown_method_input(self):
        server = ACPServer()
        raw = json.dumps({
            "jsonrpc": "2.0",
            "method": "unknown/method",
            "params": {},
            "id": 1,
        })
        resp = server._dispatch(raw)
        assert resp is not None
        data = json.loads(resp)
        assert data["error"]["code"] == ACPError.METHOD_NOT_FOUND

    def test_uninitialized_server_rejects_methods(self):
        """Server should reject methods except initialize before init."""
        server = ACPServer()
        raw = json.dumps({
            "jsonrpc": "2.0",
            "method": "session/create",
            "params": {},
            "id": 1,
        })
        resp = server._dispatch(raw)
        assert resp is not None
        data = json.loads(resp)
        assert data["error"]["code"] == ACPError.INVALID_REQUEST
        assert "not initialized" in data["error"]["message"]


class TestACPHandlerInput:
    """Test ACP handler processing of input data."""

    @pytest.mark.asyncio
    async def test_initialize_handler_input(self):
        server = ACPServer()
        params = {
            "client_info": {"name": "Zed", "version": "0.1.0", "ide_type": "zed"},
            "capabilities": {"supports_streaming": True},
        }
        result = await server._handle_initialize(params)
        assert "server_info" in result
        assert "capabilities" in result
        assert result["server_info"]["name"] == "ccb-py"

    @pytest.mark.asyncio
    async def test_session_create_input(self):
        server = ACPServer()
        server._initialized = True
        params = {
            "prompt": "Help me write code",
            "tools": ["bash", "read"],
        }
        result = await server._handle_session_create(params)
        assert "session_id" in result
        session = server.get_session(result["session_id"])
        assert session is not None
        assert session.prompt == "Help me write code"

    @pytest.mark.asyncio
    async def test_session_resume_input(self):
        server = ACPServer()
        server._initialized = True
        # Create a session first
        create_params = {"prompt": "test"}
        result = await server._handle_session_create(create_params)
        session_id = result["session_id"]

        # Resume it
        resume_params = {"session_id": session_id}
        resume_result = await server._handle_session_resume(resume_params)
        assert resume_result["session_id"] == session_id

    @pytest.mark.asyncio
    async def test_session_resume_nonexistent(self):
        server = ACPServer()
        server._initialized = True
        params = {"session_id": "nonexistent-id"}
        result = await server._handle_session_resume(params)
        assert "error" in result
        assert result["code"] == ACPError.SESSION_NOT_FOUND

    @pytest.mark.asyncio
    async def test_tool_execute_input(self):
        server = ACPServer()
        server._initialized = True
        params = {
            "name": "bash",
            "args": {"command": "echo hello"},
            "session_id": "test-session",
        }
        result = await server._handle_tool_execute(params)
        assert "status" in result


# ============================================================================
# Session Data Input Tests
# ============================================================================

class TestSessionInput:
    """Test Session data input methods."""

    def test_add_user_message_input(self):
        session = Session(id="test", cwd="/tmp")
        session.add_user_message("Hello, how are you?")
        assert len(session.messages) == 1
        msg = session.messages[0]
        assert msg.role == Role.USER
        assert msg.content == "Hello, how are you?"

    def test_add_user_message_with_images(self):
        session = Session(id="test", cwd="/tmp")
        session.add_user_message(
            "What's in this image?",
            images=[{"media_type": "image/png", "base64_data": "abc123"}],
        )
        assert len(session.messages[0].images) == 1
        assert session.messages[0].images[0]["media_type"] == "image/png"

    def test_add_user_message_with_files(self):
        session = Session(id="test", cwd="/tmp")
        session.add_user_message(
            "Review this code",
            files=[{"filename": "main.py", "content": "print('hello')"}],
        )
        assert len(session.messages[0].files) == 1
        assert session.messages[0].files[0]["filename"] == "main.py"

    def test_add_assistant_message_input(self):
        session = Session(id="test", cwd="/tmp")
        session.add_assistant_message("I can help with that!")
        assert len(session.messages) == 1
        assert session.messages[0].role == Role.ASSISTANT

    def test_add_assistant_message_with_tool_calls(self):
        session = Session(id="test", cwd="/tmp")
        tool_calls = [
            ToolCall(
                id="tc1",
                name="bash",
                input={"command": "ls -la"},
            ),
            ToolCall(
                id="tc2",
                name="read",
                input={"file_path": "/tmp/test.txt"},
            ),
        ]
        session.add_assistant_message(
            "Let me run some commands for you",
            tool_calls=tool_calls,
        )
        msg = session.messages[0]
        assert len(msg.tool_calls) == 2
        assert msg.tool_calls[0].name == "bash"
        assert msg.tool_calls[1].name == "read"

    def test_add_tool_results_input(self):
        session = Session(id="test", cwd="/tmp")
        results = [
            ToolResult(tool_use_id="tc1", content="file1.txt\nfile2.txt"),
            ToolResult(tool_use_id="tc2", content="File contents here", is_error=True),
        ]
        session.add_tool_results(results)
        msg = session.messages[0]
        assert len(msg.tool_results) == 2
        assert msg.tool_results[0].is_error is False
        assert msg.tool_results[1].is_error is True

    def test_add_usage_input(self):
        session = Session(id="test", cwd="/tmp")
        session.add_usage({"input_tokens": 500, "output_tokens": 200})
        assert session.total_input_tokens == 500
        assert session.total_output_tokens == 200
        assert session.last_input_tokens == 500


class TestMessageInputSerialization:
    """Test Message input serialization to API formats."""

    def test_to_anthropic_input(self):
        msg = Message(
            role=Role.USER,
            content="Hello",
            images=[{"media_type": "image/png", "base64_data": "xyz"}],
        )
        anthropic = msg.to_anthropic()
        assert anthropic["role"] == "user"
        assert len(anthropic["content"]) == 2  # image block + text block

    def test_to_openai_input(self):
        msg = Message(
            role=Role.ASSISTANT,
            content="Here's the answer",
        )
        openai = msg.to_openai()
        assert openai["role"] == "assistant"
        assert openai["content"] == "Here's the answer"

    def test_to_openai_with_tool_calls(self):
        msg = Message(
            role=Role.ASSISTANT,
            content="Running command...",
            tool_calls=[ToolCall(id="tc1", name="bash", input={"command": "ls"})],
        )
        openai = msg.to_openai()
        assert len(openai["tool_calls"]) == 1
        assert openai["tool_calls"][0]["function"]["name"] == "bash"


class TestACPErrorCodes:
    """Test ACP error code definitions."""

    def test_standard_error_codes(self):
        assert ACPError.PARSE_ERROR == -32700
        assert ACPError.INVALID_REQUEST == -32600
        assert ACPError.METHOD_NOT_FOUND == -32601
        assert ACPError.INVALID_PARAMS == -32602
        assert ACPError.INTERNAL_ERROR == -32603

    def test_application_error_codes(self):
        assert ACPError.SESSION_NOT_FOUND == -32000
        assert ACPError.PERMISSION_DENIED == -32001
        assert ACPError.TOOL_NOT_FOUND == -32002
        assert ACPError.SKILL_NOT_FOUND == -32003
        assert ACPError.TRANSPORT_ERROR == -32004

    def test_error_response_creation(self):
        resp = error_response(
            req_id=1,
            code=ACPError.METHOD_NOT_FOUND,
            message="Method not found",
            data={"method": "unknown"},
        )
        assert resp.error["code"] == ACPError.METHOD_NOT_FOUND
        assert resp.error["message"] == "Method not found"
        assert resp.error["data"]["method"] == "unknown"
