"""MCP client manager - discovers, connects, and manages MCP servers."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ccb.config import claude_dir, claude_json_path
from ccb.tools.base import Tool, ToolResult


@dataclass
class MCPServer:
    name: str
    type: str  # "stdio" | "http"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    process: subprocess.Popen | None = field(default=None, repr=False)
    _reader: asyncio.StreamReader | None = field(default=None, repr=False)
    _writer: asyncio.StreamWriter | None = field(default=None, repr=False)
    _proc: asyncio.subprocess.Process | None = field(default=None, repr=False)
    _request_id: int = field(default=0, repr=False)
    _pending: dict[int, asyncio.Future] = field(default_factory=dict, repr=False)
    _read_task: asyncio.Task | None = field(default=None, repr=False)
    connected: bool = False


class MCPManager:
    """Manages MCP server connections and tool proxying."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServer] = {}

    @property
    def servers(self) -> dict[str, MCPServer]:
        return self._servers

    def discover_servers(self) -> dict[str, dict[str, Any]]:
        """Find MCP server configs from ~/.claude.json and ~/.claude/settings.json."""
        configs: dict[str, dict[str, Any]] = {}

        # From ~/.claude.json
        gcfg_path = claude_json_path()
        if gcfg_path.exists():
            try:
                gcfg = json.loads(gcfg_path.read_text())
                for name, cfg in gcfg.get("mcpServers", {}).items():
                    configs[name] = cfg
            except (json.JSONDecodeError, OSError):
                pass

        # From project-level .mcp.json
        cwd_mcp = Path.cwd() / ".mcp.json"
        if cwd_mcp.exists():
            try:
                pcfg = json.loads(cwd_mcp.read_text())
                for name, cfg in pcfg.get("mcpServers", {}).items():
                    configs[name] = cfg
            except (json.JSONDecodeError, OSError):
                pass

        return configs

    async def connect_all(self) -> list[str]:
        """Discover and connect to all configured MCP servers."""
        configs = self.discover_servers()
        connected = []
        for name, cfg in configs.items():
            try:
                await self.connect(name, cfg)
                connected.append(name)
            except Exception as e:
                from ccb.display import print_error
                print_error(f"MCP {name}: {e}")
        return connected

    async def connect(self, name: str, cfg: dict[str, Any]) -> None:
        """Connect to a single MCP server."""
        server_type = cfg.get("type", "stdio")
        server = MCPServer(
            name=name,
            type=server_type,
            command=cfg.get("command"),
            args=cfg.get("args", []),
            env=cfg.get("env", {}),
            url=cfg.get("url"),
            headers=cfg.get("headers", {}),
        )

        if server_type == "stdio" and server.command:
            await self._connect_stdio(server)
        elif server_type in ("http", "sse") and server.url:
            await self._connect_http(server)
        else:
            raise ValueError(f"Unsupported MCP server type: {server_type}")

        self._servers[name] = server

    async def _connect_stdio(self, server: MCPServer) -> None:
        """Connect to stdio MCP server."""
        env = {**os.environ, **server.env}
        cmd = server.command
        args = server.args

        server._proc = await asyncio.create_subprocess_exec(
            cmd, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        server._reader = server._proc.stdout
        server._writer_raw = server._proc.stdin

        # Start reading responses
        server._read_task = asyncio.create_task(self._read_loop(server))

        # Initialize
        result = await self._send_request(server, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ccb", "version": "0.1.0"},
        })

        # Send initialized notification
        await self._send_notification(server, "notifications/initialized", {})

        # List tools
        tools_result = await self._send_request(server, "tools/list", {})
        server.tools = tools_result.get("tools", [])
        server.connected = True

    async def _connect_http(self, server: MCPServer) -> None:
        """Connect to HTTP/SSE MCP server - simplified."""
        import httpx

        url = server.url
        if not url:
            raise ValueError("HTTP MCP server requires url")

        # Try to list tools via HTTP
        async with httpx.AsyncClient(headers=server.headers, timeout=10) as client:
            # Initialize
            init_resp = await client.post(url, json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ccb", "version": "0.1.0"},
                },
            })
            init_resp.raise_for_status()

            # Initialized notification
            await client.post(url, json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            })

            # List tools
            tools_resp = await client.post(url, json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            })
            tools_resp.raise_for_status()
            data = tools_resp.json()
            server.tools = data.get("result", {}).get("tools", [])
            server.connected = True

    async def _read_loop(self, server: MCPServer) -> None:
        """Read JSONRPC responses from stdio server.

        MCP uses Content-Length framed messages (like LSP):
            Content-Length: 123\r\n
            \r\n
            {"jsonrpc":"2.0",...}
        """
        reader = server._reader
        if not reader:
            return
        while True:
            try:
                # Read headers until empty line
                content_length = -1
                while True:
                    header_line = await reader.readline()
                    if not header_line:
                        return  # EOF
                    header_str = header_line.decode("utf-8", errors="replace").strip()
                    if not header_str:
                        break  # End of headers
                    if header_str.lower().startswith("content-length:"):
                        try:
                            content_length = int(header_str.split(":", 1)[1].strip())
                        except ValueError:
                            pass

                if content_length < 0:
                    # Fallback: try newline-delimited JSON for compatibility
                    # Some servers may not use Content-Length framing
                    continue

                # Read exactly content_length bytes
                body = await reader.readexactly(content_length)
                try:
                    msg = json.loads(body)
                except json.JSONDecodeError:
                    continue

                msg_id = msg.get("id")
                if msg_id is not None and msg_id in server._pending:
                    fut = server._pending.pop(msg_id)
                    if "error" in msg:
                        fut.set_exception(Exception(
                            msg["error"].get("message", "MCP error")))
                    else:
                        fut.set_result(msg.get("result", {}))
                # Notifications (no id) are silently ignored for now

            except asyncio.IncompleteReadError:
                return  # Server closed
            except (asyncio.CancelledError, ConnectionError):
                break

    @staticmethod
    def _frame_message(payload: dict) -> bytes:
        """Encode a JSON-RPC message with Content-Length header (MCP/LSP framing)."""
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        return header + body

    async def _send_request(self, server: MCPServer, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and wait for response."""
        server._request_id += 1
        req_id = server._request_id
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        server._pending[req_id] = fut

        if server._writer_raw:
            server._writer_raw.write(self._frame_message(payload))
            await server._writer_raw.drain()

        try:
            return await asyncio.wait_for(fut, timeout=30)
        except asyncio.TimeoutError:
            server._pending.pop(req_id, None)
            raise TimeoutError(f"MCP request {method} timed out")

    async def _send_notification(self, server: MCPServer, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        if server._writer_raw:
            server._writer_raw.write(self._frame_message(payload))
            await server._writer_raw.drain()

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """Call a tool on an MCP server."""
        server = self._servers.get(server_name)
        if not server or not server.connected:
            return f"MCP server '{server_name}' not connected"

        if server.type == "stdio":
            result = await self._send_request(server, "tools/call", {
                "name": tool_name,
                "arguments": arguments,
            })
            # Extract text content
            content = result.get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            return "\n".join(texts) if texts else json.dumps(result)

        elif server.type in ("http", "sse"):
            import httpx
            async with httpx.AsyncClient(headers=server.headers, timeout=60) as client:
                resp = await client.post(server.url, json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                })
                data = resp.json()
                result = data.get("result", {})
                content = result.get("content", [])
                texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                return "\n".join(texts) if texts else json.dumps(result)

        return "Unsupported server type"

    async def list_resources(self, server_name: str | None = None) -> str:
        """List resources from MCP server(s)."""
        results = []
        servers = [self._servers[server_name]] if server_name and server_name in self._servers else self._servers.values()
        for server in servers:
            if not server.connected:
                continue
            try:
                result = await self._send_request(server, "resources/list", {})
                resources = result.get("resources", [])
                for r in resources:
                    results.append(f"  [{server.name}] {r.get('uri', '?')} - {r.get('name', '')}")
            except Exception:
                results.append(f"  [{server.name}] (resources not supported)")
        return "\n".join(results) if results else "No resources found."

    async def read_resource(self, server_name: str, uri: str) -> str:
        """Read a resource from an MCP server."""
        server = self._servers.get(server_name)
        if not server or not server.connected:
            return f"Server '{server_name}' not connected"
        result = await self._send_request(server, "resources/read", {"uri": uri})
        contents = result.get("contents", [])
        texts = [c.get("text", "") for c in contents if "text" in c]
        return "\n".join(texts) if texts else json.dumps(result)

    def get_all_tools(self) -> list[dict[str, Any]]:
        """Get all tools from all connected MCP servers, formatted for API."""
        tools = []
        for server in self._servers.values():
            if not server.connected:
                continue
            for t in server.tools:
                tools.append({
                    "name": f"mcp__{server.name}__{t['name']}",
                    "description": f"[MCP:{server.name}] {t.get('description', '')}",
                    "input_schema": t.get("inputSchema", {"type": "object", "properties": {}}),
                    "_mcp_server": server.name,
                    "_mcp_tool": t["name"],
                })
        return tools

    def parse_mcp_tool_name(self, name: str) -> tuple[str, str] | None:
        """Parse 'mcp__server__tool' into (server, tool). Returns None if not MCP."""
        if not name.startswith("mcp__"):
            return None
        parts = name.split("__", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
        return None

    # ── Resource subscription ──

    async def subscribe_resource(self, server_name: str, uri: str) -> bool:
        """Subscribe to resource changes on an MCP server."""
        server = self._servers.get(server_name)
        if not server or not server.connected:
            return False
        try:
            await self._send_request(server, "resources/subscribe", {"uri": uri})
            return True
        except Exception:
            return False

    async def unsubscribe_resource(self, server_name: str, uri: str) -> bool:
        server = self._servers.get(server_name)
        if not server or not server.connected:
            return False
        try:
            await self._send_request(server, "resources/unsubscribe", {"uri": uri})
            return True
        except Exception:
            return False

    # ── Prompts ──

    async def list_prompts(self, server_name: str) -> list[dict[str, Any]]:
        server = self._servers.get(server_name)
        if not server or not server.connected:
            return []
        try:
            result = await self._send_request(server, "prompts/list", {})
            return result.get("prompts", [])
        except Exception:
            return []

    async def get_prompt(self, server_name: str, name: str, arguments: dict[str, Any] | None = None) -> str:
        server = self._servers.get(server_name)
        if not server or not server.connected:
            return f"Server '{server_name}' not connected"
        try:
            result = await self._send_request(server, "prompts/get", {
                "name": name, "arguments": arguments or {}
            })
            messages = result.get("messages", [])
            texts = []
            for m in messages:
                c = m.get("content", {})
                if isinstance(c, dict) and c.get("type") == "text":
                    texts.append(c.get("text", ""))
                elif isinstance(c, str):
                    texts.append(c)
            return "\n".join(texts) if texts else json.dumps(result)
        except Exception as e:
            return str(e)

    # ── Config validation ──

    def validate_configs(self) -> dict[str, Any]:
        """Validate all MCP server configs before connection."""
        from ccb.mcp.config_validator import validate_all_configs
        configs = self.discover_servers()
        result = validate_all_configs(configs)
        return {
            "valid": result.valid,
            "errors": len(result.errors),
            "warnings": len(result.warnings),
            "details": result.format(),
        }

    # ── Health ──

    async def ping(self, server_name: str) -> float | None:
        """Ping a server and return latency in ms, or None on failure."""
        server = self._servers.get(server_name)
        if not server or not server.connected:
            return None
        import time
        start = time.time()
        try:
            await asyncio.wait_for(
                self._send_request(server, "ping", {}),
                timeout=10,
            )
            return (time.time() - start) * 1000
        except Exception:
            return None

    async def health_check_all(self) -> dict[str, Any]:
        """Ping all connected servers and report health."""
        results = {}
        for name, server in self._servers.items():
            latency = await self.ping(name) if server.connected else None
            results[name] = {
                "connected": server.connected,
                "type": server.type,
                "tools": len(server.tools),
                "latency_ms": round(latency, 1) if latency is not None else None,
                "healthy": latency is not None,
            }
        return results

    # ── Disconnect ──

    async def disconnect(self, server_name: str) -> bool:
        """Disconnect a single MCP server."""
        server = self._servers.get(server_name)
        if not server:
            return False
        if server._read_task:
            server._read_task.cancel()
        if server._proc:
            try:
                server._proc.terminate()
                await asyncio.wait_for(server._proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                if server._proc:
                    server._proc.kill()
        server.connected = False
        del self._servers[server_name]
        return True

    async def disconnect_all(self) -> None:
        """Disconnect all MCP servers."""
        for server in self._servers.values():
            if server._read_task:
                server._read_task.cancel()
            if server._proc:
                try:
                    server._proc.terminate()
                    await asyncio.wait_for(server._proc.wait(), timeout=5)
                except (asyncio.TimeoutError, ProcessLookupError):
                    if server._proc:
                        server._proc.kill()
            server.connected = False
        self._servers.clear()

    # ── Reconnect ──

    async def reconnect(self, server_name: str) -> bool:
        """Reconnect a specific MCP server."""
        configs = self.discover_servers()
        cfg = configs.get(server_name)
        if not cfg:
            return False
        await self.disconnect(server_name)
        try:
            await self.connect(server_name, cfg)
            return True
        except Exception:
            return False

    async def reconnect_all(self) -> list[str]:
        """Reconnect all MCP servers."""
        await self.disconnect_all()
        return await self.connect_all()
