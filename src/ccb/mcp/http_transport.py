"""HTTP/SSE transport for MCP (Model Context Protocol).

Supports both legacy SSE and the newer Streamable HTTP transport.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator
from urllib.parse import urljoin

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


class MCPHttpTransport:
    """MCP client over HTTP/SSE."""

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout
        self._session: Any = None
        self._message_endpoint: str = ""
        self._sse_endpoint: str = ""

    async def connect(self) -> None:
        if not HAS_AIOHTTP:
            raise RuntimeError("aiohttp required for HTTP transport: pip install aiohttp")
        self._session = aiohttp.ClientSession(
            headers=self.headers,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        )
        # Try Streamable HTTP first, fall back to legacy SSE
        try:
            await self._connect_streamable()
        except Exception:
            await self._connect_legacy_sse()

    async def _connect_streamable(self) -> None:
        """Streamable HTTP: POST to /mcp endpoint."""
        self._message_endpoint = f"{self.base_url}/mcp"
        # Send initialize
        resp = await self._session.post(
            self._message_endpoint,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "ccb-py", "version": "1.0.0"},
            }},
            headers={"Accept": "application/json, text/event-stream"},
        )
        if resp.status != 200:
            raise ConnectionError(f"Streamable HTTP failed: {resp.status}")
        data = await resp.json()
        if "result" not in data:
            raise ConnectionError("Invalid initialize response")

    async def _connect_legacy_sse(self) -> None:
        """Legacy SSE: GET /sse for events, POST /messages for requests."""
        self._sse_endpoint = f"{self.base_url}/sse"
        self._message_endpoint = f"{self.base_url}/messages"

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC request and return the result."""
        if not self._session:
            raise RuntimeError("Not connected")
        req_id = str(uuid.uuid4())[:8]
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params:
            payload["params"] = params
        resp = await self._session.post(
            self._message_endpoint,
            json=payload,
            headers={"Accept": "application/json"},
        )
        if resp.status != 200:
            raise RuntimeError(f"MCP request failed: {resp.status}")
        data = await resp.json()
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data.get("result")

    async def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._session:
            raise RuntimeError("Not connected")
        payload = {"jsonrpc": "2.0", "method": method}
        if params:
            payload["params"] = params
        await self._session.post(self._message_endpoint, json=payload)

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self.send_request("tools/list")
        return result.get("tools", []) if result else []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return await self.send_request("tools/call", {"name": name, "arguments": arguments or {}})

    async def list_resources(self) -> list[dict[str, Any]]:
        result = await self.send_request("resources/list")
        return result.get("resources", []) if result else []

    async def read_resource(self, uri: str) -> Any:
        return await self.send_request("resources/read", {"uri": uri})

    async def list_prompts(self) -> list[dict[str, Any]]:
        result = await self.send_request("prompts/list")
        return result.get("prompts", []) if result else []

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return await self.send_request("prompts/get", {"name": name, "arguments": arguments or {}})

    async def subscribe_resource(self, uri: str) -> None:
        await self.send_request("resources/subscribe", {"uri": uri})

    async def unsubscribe_resource(self, uri: str) -> None:
        await self.send_request("resources/unsubscribe", {"uri": uri})

    async def stream_sse(self) -> AsyncIterator[dict[str, Any]]:
        """Listen for SSE events (legacy transport)."""
        if not self._session or not self._sse_endpoint:
            return
        async with self._session.get(self._sse_endpoint) as resp:
            buffer = ""
            async for chunk in resp.content:
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n\n" in buffer:
                    event_str, buffer = buffer.split("\n\n", 1)
                    data_line = ""
                    for line in event_str.splitlines():
                        if line.startswith("data: "):
                            data_line = line[6:]
                    if data_line:
                        try:
                            yield json.loads(data_line)
                        except json.JSONDecodeError:
                            pass

    async def __aenter__(self) -> MCPHttpTransport:
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
