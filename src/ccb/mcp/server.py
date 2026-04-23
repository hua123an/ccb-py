"""MCP Server mode for ccb-py.

Exposes ccb-py's tools, resources, and prompts over the MCP protocol
so external clients (IDE extensions, other agents) can call them.

Supports both stdio and HTTP transport.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Callable, Awaitable


ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


class MCPServer:
    """A minimal MCP server implementation."""

    def __init__(self, name: str = "ccb-py", version: str = "1.0.0"):
        self.name = name
        self.version = version
        self._tools: dict[str, dict[str, Any]] = {}
        self._tool_handlers: dict[str, ToolHandler] = {}
        self._resources: dict[str, dict[str, Any]] = {}
        self._prompts: dict[str, dict[str, Any]] = {}
        self._initialized = False

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
    ) -> None:
        self._tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
        }
        self._tool_handlers[name] = handler

    def register_resource(self, uri: str, name: str, description: str = "", mime_type: str = "text/plain") -> None:
        self._resources[uri] = {
            "uri": uri,
            "name": name,
            "description": description,
            "mimeType": mime_type,
        }

    def register_prompt(self, name: str, description: str, arguments: list[dict[str, Any]] | None = None) -> None:
        self._prompts[name] = {
            "name": name,
            "description": description,
            "arguments": arguments or [],
        }

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Process a JSON-RPC request and return a response."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        # Notifications (no id) don't need a response
        if req_id is None:
            await self._handle_notification(method, params)
            return None

        try:
            result = await self._dispatch(method, params)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            return {
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32603, "message": str(e)},
            }

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            self._initialized = True
            return {
                "protocolVersion": "2025-03-26",
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": self.name, "version": self.version},
            }
        if method == "tools/list":
            return {"tools": list(self._tools.values())}
        if method == "tools/call":
            return await self._call_tool(params)
        if method == "resources/list":
            return {"resources": list(self._resources.values())}
        if method == "resources/read":
            return await self._read_resource(params)
        if method == "prompts/list":
            return {"prompts": list(self._prompts.values())}
        if method == "prompts/get":
            return self._get_prompt(params)
        if method == "ping":
            return {}
        raise ValueError(f"Unknown method: {method}")

    async def _call_tool(self, params: dict[str, Any]) -> Any:
        name = params.get("name", "")
        handler = self._tool_handlers.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name}")
        arguments = params.get("arguments", {})
        result = await handler(arguments)
        if isinstance(result, str):
            return {"content": [{"type": "text", "text": result}]}
        return result

    async def _read_resource(self, params: dict[str, Any]) -> Any:
        uri = params.get("uri", "")
        if uri not in self._resources:
            raise ValueError(f"Unknown resource: {uri}")
        # Default: return empty content (subclass should override)
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": ""}]}

    def _get_prompt(self, params: dict[str, Any]) -> Any:
        name = params.get("name", "")
        if name not in self._prompts:
            raise ValueError(f"Unknown prompt: {name}")
        return {"description": self._prompts[name]["description"], "messages": []}

    async def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "notifications/initialized":
            pass  # Client confirmed initialization
        elif method == "notifications/cancelled":
            pass  # Client cancelled a request

    # ── Stdio transport ──

    async def serve_stdio(self) -> None:
        """Serve MCP over stdin/stdout (JSON-RPC line protocol)."""
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                request = json.loads(line.decode())
            except json.JSONDecodeError:
                continue

            response = await self.handle_request(request)
            if response is not None:
                sys.stdout.buffer.write((json.dumps(response) + "\n").encode())
                sys.stdout.buffer.flush()

    # ── HTTP transport ──

    async def serve_http(self, host: str = "127.0.0.1", port: int = 3100) -> None:
        """Serve MCP over HTTP (Streamable HTTP transport)."""
        try:
            from aiohttp import web
        except ImportError:
            raise RuntimeError("aiohttp required for HTTP server: pip install aiohttp")

        async def handle_mcp(request: web.Request) -> web.Response:
            try:
                body = await request.json()
            except Exception:
                return web.json_response(
                    {"error": {"code": -32700, "message": "Parse error"}}, status=400
                )
            response = await self.handle_request(body)
            if response is None:
                return web.Response(status=204)
            return web.json_response(response)

        async def handle_health(request: web.Request) -> web.Response:
            return web.json_response({"status": "ok", "server": self.name})

        app = web.Application()
        app.router.add_post("/mcp", handle_mcp)
        app.router.add_get("/health", handle_health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        print(f"MCP server listening on http://{host}:{port}")
        # Run forever
        await asyncio.Event().wait()


def create_default_server() -> MCPServer:
    """Create an MCP server with ccb-py's built-in tools registered."""
    server = MCPServer()

    async def handle_echo(args: dict[str, Any]) -> str:
        return args.get("text", "")

    server.register_tool(
        "echo", "Echo the input text",
        {"type": "object", "properties": {"text": {"type": "string"}}},
        handle_echo,
    )
    server.register_resource("ccb://version", "version", "ccb-py version info")
    server.register_prompt("code-review", "Review code for issues", [
        {"name": "code", "description": "The code to review", "required": True},
    ])
    return server
