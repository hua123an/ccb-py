"""Agent Communication Protocol (ACP) for IDE integration.

Implements a JSON-RPC 2.0 based protocol server and client for Zed, Cursor,
VS Code, and other IDE integrations.  Supports both stdio (for VS Code) and
TCP (for Zed) transports.

Message types handled:
  initialize, session/create, session/resume, session/list,
  tool/execute, permission/check, skill/list, skill/invoke
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

from ccb.tools.base import create_default_registry, validate_input

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON-RPC 2.0 types
# ---------------------------------------------------------------------------

JSONRPC_VERSION = "2.0"


@dataclass
class JSONRPCRequest:
    """Inbound JSON-RPC 2.0 request."""
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str | int | None = None
    jsonrpc: str = JSONRPC_VERSION

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params:
            d["params"] = self.params
        if self.id is not None:
            d["id"] = self.id
        return d


@dataclass
class JSONRPCResponse:
    """Outbound JSON-RPC 2.0 response."""
    id: str | int | None = None
    result: Any = None
    error: dict[str, Any] | None = None
    jsonrpc: str = JSONRPC_VERSION

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            d["error"] = self.error
        else:
            d["result"] = self.result
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class JSONRPCNotification:
    """Outbound JSON-RPC 2.0 notification (no id, no response expected)."""
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = JSONRPC_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {"jsonrpc": self.jsonrpc, "method": self.method, "params": self.params}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# ACP error codes
# ---------------------------------------------------------------------------

class ACPError:
    """Standard JSON-RPC / ACP error codes."""
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # Application-defined
    SESSION_NOT_FOUND = -32000
    PERMISSION_DENIED = -32001
    TOOL_NOT_FOUND = -32002
    SKILL_NOT_FOUND = -32003
    TRANSPORT_ERROR = -32004


def error_response(
    req_id: str | int | None,
    code: int,
    message: str,
    data: Any = None,
) -> JSONRPCResponse:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return JSONRPCResponse(id=req_id, error=err)


# ---------------------------------------------------------------------------
# Message / notification types
# ---------------------------------------------------------------------------

class MessageType(str, Enum):
    SESSION_START = "session_start"
    SESSION_RESUME = "session_resume"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_GRANT = "permission_grant"
    SKILL_LIST = "skill_list"
    SKILL_INVOKE = "skill_invoke"


# ---------------------------------------------------------------------------
# Transport abstractions
# ---------------------------------------------------------------------------

class Transport(ABC):
    """Abstract bidirectional message transport."""

    @abstractmethod
    async def send(self, message: str) -> None:
        ...

    @abstractmethod
    async def receive(self) -> str | None:
        ...

    async def close(self) -> None:
        pass

    @property
    def is_connected(self) -> bool:
        return True


class StdioTransport(Transport):
    """JSON-RPC over stdin/stdout (for VS Code extension host)."""

    def __init__(
        self,
        reader: asyncio.StreamReader | None = None,
        writer: asyncio.StreamWriter | None = None,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._closed = False
        self._stdin_queue: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, message: str) -> None:
        if self._closed:
            return
        if self._writer is not None:
            self._writer.write((message + "\n").encode())
            await self._writer.drain()
        else:
            import sys
            sys.stdout.write(message + "\n")
            sys.stdout.flush()

    async def receive(self) -> str | None:
        if self._closed:
            return None
        if self._reader is not None:
            line = await self._reader.readline()
            if not line:
                return None
            return line.decode().strip()
        try:
            return await asyncio.wait_for(self._stdin_queue.get(), timeout=300)
        except asyncio.TimeoutError:
            return None

    async def feed_line(self, line: str) -> None:
        """Feed a line to the stdin queue (for programmatic use)."""
        await self._stdin_queue.put(line)

    async def close(self) -> None:
        self._closed = True

    @property
    def is_connected(self) -> bool:
        return not self._closed


class TCPTransport(Transport):
    """JSON-RPC over a TCP socket (for Zed)."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3100,
        reader: asyncio.StreamReader | None = None,
        writer: asyncio.StreamWriter | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._reader = reader
        self._writer = writer
        self._closed = False

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self.host, self.port
        )

    async def send(self, message: str) -> None:
        if self._closed or self._writer is None:
            raise ConnectionError("TCP transport not connected")
        self._writer.write((message + "\n").encode())
        await self._writer.drain()

    async def receive(self) -> str | None:
        if self._closed or self._reader is None:
            return None
        line = await self._reader.readline()
        if not line:
            return None
        return line.decode().strip()

    async def close(self) -> None:
        self._closed = True
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

    @property
    def is_connected(self) -> bool:
        return not self._closed and self._writer is not None


# ---------------------------------------------------------------------------
# Capability negotiation
# ---------------------------------------------------------------------------

@dataclass
class ClientInfo:
    name: str
    version: str = ""
    ide_type: str = ""  # "vscode", "zed", "cursor", etc.


@dataclass
class ServerCapabilities:
    """Capabilities advertised by the ACP server."""
    tools: bool = True
    skills: bool = True
    permissions: bool = True
    session_management: bool = True
    streaming: bool = False
    supported_transports: list[str] = field(default_factory=lambda: ["stdio", "tcp"])
    protocol_version: str = "1.0.0"


@dataclass
class ClientCapabilities:
    """Capabilities advertised by the IDE client."""
    supports_streaming: bool = False
    supports_progress: bool = False
    supports_cancellation: bool = False
    ide_type: str = ""


# ---------------------------------------------------------------------------
# Session state for ACP
# ---------------------------------------------------------------------------

@dataclass
class ACPSessionState:
    """Internal session state tracked by the ACP server."""
    session_id: str
    prompt: str = ""
    cwd: str = "."
    tools: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_state: dict[str, Any] = field(default_factory=dict)
    cursor_position: dict[str, Any] = field(default_factory=dict)
    connected_ides: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Handler type
# ---------------------------------------------------------------------------

MethodHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, Any]]


class _AwaitableString(str):
    """String response that can also be awaited by async dispatch callers."""

    def __await__(self):
        async def _return_self() -> str:
            return str(self)

        return _return_self().__await__()


async def _immediate_response(response: str | None) -> str | None:
    return response


# ---------------------------------------------------------------------------
# ACP Server
# ---------------------------------------------------------------------------

class ACPServer:
    """Agent Communication Protocol server.

    Implements JSON-RPC 2.0 over pluggable transports (stdio or TCP).
    Registers method handlers and dispatches inbound requests.
    """

    PROTOCOL_VERSION = "1.0.0"
    SERVER_NAME = "ccb-py"

    def __init__(self, transport: Transport | None = None) -> None:
        self.transport = transport
        self._handlers: dict[str, MethodHandler] = {}
        self._sessions: dict[str, ACPSessionState] = {}
        self._client_info: ClientInfo | None = None
        self._client_capabilities: ClientCapabilities | None = None
        self._initialized = False
        self._running = False
        self._notification_queue: asyncio.Queue[JSONRPCNotification] = asyncio.Queue()
        self._register_core_handlers()

    # -- Handler registration --

    def on(self, method: str, handler: MethodHandler) -> None:
        """Register a handler for a JSON-RPC method."""
        self._handlers[method] = handler

    def _register_core_handlers(self) -> None:
        self.on("initialize", self._handle_initialize)
        self.on("session/create", self._handle_session_create)
        self.on("session/resume", self._handle_session_resume)
        self.on("session/list", self._handle_session_list)
        self.on("tool/execute", self._handle_tool_execute)
        self.on("permission/check", self._handle_permission_check)
        self.on("skill/list", self._handle_skill_list)
        self.on("skill/invoke", self._handle_skill_invoke)

    # -- Core method implementations --

    async def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle capability negotiation."""
        self._client_info = ClientInfo(
            name=params.get("client_info", {}).get("name", "unknown"),
            version=params.get("client_info", {}).get("version", ""),
            ide_type=params.get("client_info", {}).get("ide_type", ""),
        )
        self._client_capabilities = ClientCapabilities(
            supports_streaming=params.get("capabilities", {}).get("supports_streaming", False),
            supports_progress=params.get("capabilities", {}).get("supports_progress", False),
            supports_cancellation=params.get("capabilities", {}).get("supports_cancellation", False),
            ide_type=self._client_info.ide_type,
        )
        self._initialized = True
        caps = ServerCapabilities()
        return {
            "server_info": {
                "name": self.SERVER_NAME,
                "version": self.PROTOCOL_VERSION,
            },
            "capabilities": {
                "tools": caps.tools,
                "skills": caps.skills,
                "permissions": caps.permissions,
                "session_management": caps.session_management,
                "streaming": caps.streaming,
                "supported_transports": caps.supported_transports,
                "protocol_version": caps.protocol_version,
            },
        }

    async def _handle_session_create(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a new ACP session."""
        session_id = str(uuid.uuid4())
        ide_type = self._client_info.ide_type if self._client_info else "unknown"
        state = ACPSessionState(
            session_id=session_id,
            prompt=params.get("prompt", ""),
            cwd=params.get("cwd", "."),
            tools=params.get("tools", []),
            connected_ides=[ide_type] if ide_type else [],
        )
        self._sessions[session_id] = state
        await self._emit_notification(MessageType.SESSION_START, {
            "session_id": session_id,
            "ide_type": ide_type,
        })
        return {"session_id": session_id}

    async def _handle_session_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        """Resume an existing ACP session."""
        session_id = params.get("session_id", "")
        state = self._sessions.get(session_id)
        if state is None:
            return {"error": "session_not_found", "code": ACPError.SESSION_NOT_FOUND}
        ide_type = self._client_info.ide_type if self._client_info else "unknown"
        if ide_type and ide_type not in state.connected_ides:
            state.connected_ides.append(ide_type)
        state.updated_at = time.time()
        await self._emit_notification(MessageType.SESSION_RESUME, {
            "session_id": session_id,
            "ide_type": ide_type,
        })
        return {
            "session_id": session_id,
            "cwd": state.cwd,
            "messages": state.messages,
            "tool_state": state.tool_state,
            "cursor_position": state.cursor_position,
        }

    async def _handle_session_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """List all active ACP sessions."""
        sessions = []
        for sid, state in self._sessions.items():
            sessions.append({
                "session_id": sid,
                "prompt": state.prompt,
                "cwd": state.cwd,
                "tools": state.tools,
                "created_at": state.created_at,
                "updated_at": state.updated_at,
                "connected_ides": state.connected_ides,
            })
        return {"sessions": sessions}

    async def _handle_tool_execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool by name.  Actual tool dispatch is delegated via override or callback."""
        tool_name = params.get("name", "")
        tool_args = params.get("args", {})
        session_id = params.get("session_id", "")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ACPClientError("Missing or invalid tool name", ACPError.INVALID_PARAMS)
        if not isinstance(tool_args, dict):
            raise ACPClientError("Tool args must be an object", ACPError.INVALID_PARAMS)
        state = self._sessions.get(session_id) if session_id else None
        cwd = state.cwd if state and state.cwd else "."
        registry = create_default_registry(cwd)
        tool = registry.get(tool_name)
        if tool is not None:
            validation_errors = validate_input(tool_args, tool.input_schema)
            if validation_errors:
                raise ACPClientError(
                    "Invalid tool input: " + "; ".join(validation_errors),
                    ACPError.INVALID_PARAMS,
                )
        await self._emit_notification(MessageType.TOOL_CALL, {
            "name": tool_name,
            "args": tool_args,
            "session_id": session_id,
        })
        result = await self.execute_tool(tool_name, tool_args, session_id)
        await self._emit_notification(MessageType.TOOL_RESULT, {
            "name": tool_name,
            "result": result,
            "session_id": session_id,
        })
        return result

    async def execute_tool(
        self, name: str, args: dict[str, Any], session_id: str = ""
    ) -> dict[str, Any]:
        """Override in subclass or set via on() to provide tool execution."""
        return {"status": "not_implemented", "tool": name}

    async def _handle_permission_check(self, params: dict[str, Any]) -> dict[str, Any]:
        """Check whether a tool call is permitted."""
        tool_name = params.get("tool", "")
        tool_args = params.get("args", {})
        session_id = params.get("session_id", "")
        return await self.check_permission(tool_name, tool_args, session_id)

    async def check_permission(
        self, tool: str, args: dict[str, Any], session_id: str = ""
    ) -> dict[str, Any]:
        """Override or set via on() to provide permission checking.

        Returns {"decision": "allowed" | "denied" | "ask"}.
        """
        try:
            from ccb.permissions import needs_permission
            state = self._sessions.get(session_id) if session_id else None
            cwd = state.cwd if state and state.cwd else ""
            if needs_permission(tool, args, cwd=cwd):
                return {"decision": "ask"}
            return {"decision": "allowed"}
        except ImportError:
            return {"decision": "allowed"}

    async def _handle_skill_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """List available skills."""
        cwd = params.get("cwd", ".")
        kind = params.get("kind")
        skills = await self.list_skills(cwd, kind=kind)
        return {"skills": skills}

    async def list_skills(
        self,
        cwd: str = ".",
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """Override or use the default ccb skill loader."""
        try:
            from ccb.skills import list_skills, normalize_skill_kind, skill_metadata
            wanted_kind = normalize_skill_kind(kind)
            return [skill_metadata(s) for s in list_skills(cwd, kind=wanted_kind)]
        except ImportError:
            return []

    async def _handle_skill_invoke(self, params: dict[str, Any]) -> dict[str, Any]:
        """Invoke a skill by name."""
        skill_name = params.get("name", "")
        skill_args = params.get("args", {})
        await self._emit_notification(MessageType.SKILL_INVOKE, {
            "name": skill_name,
            "args": skill_args,
        })
        return await self.invoke_skill(skill_name, skill_args)

    async def invoke_skill(
        self, name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Default skill invocation backed by the local ccb skill loader."""
        from ccb.skills import normalize_skill_kind, resolve_skill_prompt, skill_metadata

        if not isinstance(name, str) or not name.strip():
            raise ACPClientError("Missing or invalid skill name", ACPError.INVALID_PARAMS)

        cwd = str(args.get("cwd") or ".")
        try:
            kind = normalize_skill_kind(args.get("kind"))
        except ValueError as exc:
            raise ACPClientError(str(exc), ACPError.INVALID_PARAMS) from exc

        prompt_args = args.get("prompt_args")
        if prompt_args is None:
            prompt_args = args.get("text") or args.get("arguments") or ""

        resolved = resolve_skill_prompt(cwd, name, prompt_args, kind=kind)
        if resolved is None:
            raise ACPClientError(f"Skill not found: {name}", ACPError.SKILL_NOT_FOUND)
        skill, prompt = resolved

        return {
            "status": "ok",
            "skill": skill_metadata(skill),
            "prompt": prompt,
        }

    # -- Notifications --

    async def _emit_notification(
        self, msg_type: MessageType, params: dict[str, Any]
    ) -> None:
        """Queue a notification for delivery to the client."""
        notif = JSONRPCNotification(
            method=f"acp/{msg_type.value}",
            params=params,
        )
        await self._notification_queue.put(notif)

    # -- Request dispatch --

    def _dispatch(self, raw: str) -> Coroutine[Any, Any, str | None] | _AwaitableString | None:
        """Parse and dispatch a single JSON-RPC message. Returns response JSON."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return _immediate_response(
                error_response(None, ACPError.PARSE_ERROR, "Parse error").to_json()
            )

        req_id = data.get("id")
        method = data.get("method", "")
        params = data.get("params", {})

        if not method:
            return _AwaitableString(
                error_response(req_id, ACPError.INVALID_REQUEST, "Missing method").to_json()
            )

        handler = self._handlers.get(method)
        if handler is None:
            return _AwaitableString(
                error_response(
                    req_id, ACPError.METHOD_NOT_FOUND, f"Method not found: {method}"
                ).to_json()
            )

        # Gate non-initialize methods behind initialization
        if method != "initialize" and not self._initialized:
            return _AwaitableString(
                error_response(
                    req_id, ACPError.INVALID_REQUEST, "Server not initialized"
                ).to_json()
            )

        return self._dispatch_async(req_id, method, params, handler)

    async def _dispatch_async(
        self,
        req_id: str | int | None,
        method: str,
        params: dict[str, Any],
        handler: MethodHandler,
    ) -> str | None:
        try:
            result = await handler(params)
        except ACPClientError as exc:
            return error_response(req_id, exc.code, str(exc)).to_json()
        except Exception as exc:
            logger.exception("Handler error for %s", method)
            return error_response(
                req_id, ACPError.INTERNAL_ERROR, str(exc)
            ).to_json()

        # Notifications have no id; do not reply
        if req_id is None:
            return None

        return JSONRPCResponse(id=req_id, result=result).to_json()

    # -- Main server loops --

    async def serve_stdio(self) -> None:
        """Serve over stdio (stdin/stdout) for VS Code."""
        transport = self.transport or StdioTransport()
        self.transport = transport
        self._running = True
        logger.info("ACP server listening on stdio")

        while self._running and transport.is_connected:
            line = await transport.receive()
            if line is None:
                break
            resp = await self._dispatch(line)
            if resp is not None:
                await transport.send(resp)
                # Drain any pending notifications
                while not self._notification_queue.empty():
                    notif = self._notification_queue.get_nowait()
                    await transport.send(notif.to_json())

        await transport.close()

    async def serve_tcp(self, host: str = "127.0.0.1", port: int = 3100) -> None:
        """Serve over TCP for Zed and similar IDEs."""
        self._running = True

        async def handle_client(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            transport = TCPTransport(host=host, port=port, reader=reader, writer=writer)
            logger.info("ACP client connected from %s", writer.get_extra_info("peername"))
            try:
                while self._running and transport.is_connected:
                    line = await transport.receive()
                    if line is None:
                        break
                    resp = await self._dispatch(line)
                    if resp is not None:
                        await transport.send(resp)
                        while not self._notification_queue.empty():
                            notif = self._notification_queue.get_nowait()
                            await transport.send(notif.to_json())
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                await transport.close()
                logger.info("ACP client disconnected")

        server = await asyncio.start_server(handle_client, host, port)
        logger.info("ACP server listening on %s:%d", host, port)
        async with server:
            await server.serve_forever()

    def stop(self) -> None:
        self._running = False

    # -- Session state access --

    def get_session(self, session_id: str) -> ACPSessionState | None:
        return self._sessions.get(session_id)

    def update_session(
        self,
        session_id: str,
        *,
        messages: list[dict[str, Any]] | None = None,
        tool_state: dict[str, Any] | None = None,
        cursor_position: dict[str, Any] | None = None,
    ) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        if messages is not None:
            state.messages = messages
        if tool_state is not None:
            state.tool_state = tool_state
        if cursor_position is not None:
            state.cursor_position = cursor_position
        state.updated_at = time.time()
        return True

    def remove_session(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None


# ---------------------------------------------------------------------------
# ACP Client
# ---------------------------------------------------------------------------

class ACPClientError(Exception):
    """Error returned by the ACP server."""

    def __init__(self, message: str, code: int = ACPError.INTERNAL_ERROR) -> None:
        super().__init__(message)
        self.code = code


class ACPClient:
    """Client for connecting to an ACP server from an IDE or agent."""

    def __init__(self, transport: Transport | None = None) -> None:
        self.transport = transport
        self._server_info: dict[str, Any] = {}
        self._server_capabilities: dict[str, Any] = {}
        self._initialized = False
        self._pending: dict[str | int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._notifications: asyncio.Queue[JSONRPCNotification] = asyncio.Queue()

    def _next_request_id(self) -> int:
        rid = self._next_id
        self._next_id += 1
        return rid

    async def connect_stdio(self) -> None:
        """Connect to a server via stdin/stdout."""
        self.transport = StdioTransport()

    async def connect_tcp(self, host: str = "127.0.0.1", port: int = 3100) -> None:
        """Connect to a server via TCP."""
        transport = TCPTransport(host=host, port=port)
        await transport.connect()
        self.transport = transport

    async def _send_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for the response."""
        if self.transport is None:
            raise ConnectionError("Not connected")

        req_id = self._next_request_id()
        msg = JSONRPCRequest(id=req_id, method=method, params=params or {})
        await self.transport.send(json.dumps(msg.to_dict(), ensure_ascii=False))

        # Read responses until we get ours
        while True:
            raw = await self.transport.receive()
            if raw is None:
                raise ConnectionError("Connection closed")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if "id" in data and data["id"] == req_id:
                if "error" in data:
                    err = data["error"]
                    raise ACPClientError(
                        err.get("message", "Unknown error"),
                        err.get("code", ACPError.INTERNAL_ERROR),
                    )
                return data.get("result", {})
            # Notification
            if "id" not in data:
                notif = JSONRPCNotification(
                    method=data.get("method", ""),
                    params=data.get("params", {}),
                )
                await self._notifications.put(notif)

    async def initialize(
        self,
        client_name: str = "ccb-py-client",
        client_version: str = "1.0.0",
        ide_type: str = "unknown",
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform capability negotiation with the server."""
        result = await self._send_request("initialize", {
            "client_info": {
                "name": client_name,
                "version": client_version,
                "ide_type": ide_type,
            },
            "capabilities": capabilities or {},
        })
        self._server_info = result.get("server_info", {})
        self._server_capabilities = result.get("capabilities", {})
        self._initialized = True
        return result

    async def session_create(
        self, prompt: str = "", tools: list[str] | None = None
    ) -> str:
        """Create a new session on the server. Returns session_id."""
        result = await self._send_request("session/create", {
            "prompt": prompt,
            "tools": tools or [],
        })
        return result.get("session_id", "")

    async def session_resume(self, session_id: str) -> dict[str, Any]:
        """Resume an existing session."""
        return await self._send_request("session/resume", {
            "session_id": session_id,
        })

    async def session_list(self) -> list[dict[str, Any]]:
        """List all sessions on the server."""
        result = await self._send_request("session/list", {})
        return result.get("sessions", [])

    async def tool_execute(
        self, name: str, args: dict[str, Any], session_id: str = ""
    ) -> dict[str, Any]:
        """Execute a tool on the server."""
        return await self._send_request("tool/execute", {
            "name": name,
            "args": args,
            "session_id": session_id,
        })

    async def permission_check(
        self, tool: str, args: dict[str, Any], session_id: str = ""
    ) -> dict[str, Any]:
        """Check permission for a tool call."""
        return await self._send_request("permission/check", {
            "tool": tool,
            "args": args,
            "session_id": session_id,
        })

    async def skill_list(
        self,
        cwd: str = ".",
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """List available skills, optionally filtered by kind."""
        params: dict[str, Any] = {"cwd": cwd}
        if kind:
            params["kind"] = kind
        result = await self._send_request("skill/list", params)
        return result.get("skills", [])

    async def skill_invoke(
        self, name: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Invoke a skill by name.

        Useful args:
          cwd: project root for lookup
          kind: "skill" | "workflow"
          prompt_args: structured arguments merged into the generated prompt
          text / arguments: string fallback when prompt_args is omitted
        """
        return await self._send_request("skill/invoke", {
            "name": name,
            "args": args or {},
        })

    async def close(self) -> None:
        if self.transport is not None:
            await self.transport.close()


# ---------------------------------------------------------------------------
# In-memory transport for testing
# ---------------------------------------------------------------------------

class InMemoryTransport(Transport):
    """Pairs two endpoints for in-process testing without real I/O.

    Use ``pair()`` to create matched (server_transport, client_transport) that
    are wired together.
    """

    def __init__(self) -> None:
        self._inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._closed = False

    async def send(self, message: str) -> None:
        await self._inbox.put(message)

    async def receive(self) -> str | None:
        if self._closed:
            return None
        try:
            return await asyncio.wait_for(self._inbox.get(), timeout=5)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        self._closed = True
        await self._inbox.put(None)

    @property
    def is_connected(self) -> bool:
        return not self._closed

    @staticmethod
    def pair() -> tuple[InMemoryTransport, InMemoryTransport]:
        """Create a matched pair of transports wired to each other.

        Returns (server_transport, client_transport) where writing to one
        makes the message readable on the other.
        """
        server_to_client: asyncio.Queue[str | None] = asyncio.Queue()
        client_to_server: asyncio.Queue[str | None] = asyncio.Queue()

        class _Side(InMemoryTransport):
            def __init__(self, inbox: asyncio.Queue, outbox: asyncio.Queue) -> None:
                super().__init__()
                self._inbox = inbox
                self._outbox = outbox

            async def send(self, message: str) -> None:
                await self._outbox.put(message)

            async def receive(self) -> str | None:
                if self._closed:
                    return None
                try:
                    return await asyncio.wait_for(self._inbox.get(), timeout=5)
                except asyncio.TimeoutError:
                    return None

        server_side = _Side(inbox=client_to_server, outbox=server_to_client)
        client_side = _Side(inbox=server_to_client, outbox=client_to_server)
        return server_side, client_side
