"""HTTP API server mode for ccb-py.

Exposes a REST API so IDE extensions and other tools can interact
with ccb-py programmatically.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any


class APIServer:
    """HTTP API server for ccb-py."""

    def __init__(self, host: str = "127.0.0.1", port: int = 3300):
        self.host = host
        self.port = port
        self._sessions: dict[str, dict[str, Any]] = {}
        self._ws_clients: dict[str, Any] = {}
        self._api_key: str = ""
        self._running = False

    async def start(self) -> None:
        try:
            from aiohttp import web
        except ImportError:
            raise RuntimeError("aiohttp required: pip install aiohttp")

        app = web.Application()
        app.router.add_get("/health", self._health)
        app.router.add_post("/v1/chat", self._chat)
        app.router.add_post("/v1/query", self._query)
        app.router.add_get("/v1/sessions", self._list_sessions)
        app.router.add_get("/v1/session/{sid}", self._get_session)
        app.router.add_post("/v1/command", self._run_command)
        app.router.add_get("/v1/status", self._status)
        app.router.add_get("/v1/tools", self._list_tools)
        app.router.add_post("/v1/tool/{name}", self._call_tool)
        app.router.add_get("/v1/ws", self._websocket)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        self._running = True
        print(f"ccb-py API server: http://{self.host}:{self.port}")

    async def _health(self, request: Any) -> Any:
        from aiohttp import web
        return web.json_response({"status": "ok", "version": "1.0.0"})

    async def _status(self, request: Any) -> Any:
        from aiohttp import web
        return web.json_response({
            "running": True,
            "sessions": len(self._sessions),
            "uptime": time.time(),
        })

    async def _chat(self, request: Any) -> Any:
        """Send a message and get a streaming response."""
        from aiohttp import web
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        prompt = body.get("message", "")
        session_id = body.get("session_id", str(uuid.uuid4())[:8])

        if not prompt:
            return web.json_response({"error": "No message"}, status=400)

        try:
            from ccb.query_engine import run_query
            result = await run_query(prompt)
            return web.json_response({
                "session_id": session_id,
                "response": result,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _query(self, request: Any) -> Any:
        """Non-interactive query (pipe mode equivalent)."""
        from aiohttp import web
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        prompt = body.get("prompt", "")
        model = body.get("model")
        system = body.get("system_prompt")

        if not prompt:
            return web.json_response({"error": "No prompt"}, status=400)

        try:
            from ccb.query_engine import run_query
            result = await run_query(prompt, model=model, system_prompt=system)
            return web.json_response({"result": result})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _list_sessions(self, request: Any) -> Any:
        from aiohttp import web
        from ccb.session import list_sessions
        sessions = list_sessions()
        return web.json_response({"sessions": sessions})

    async def _get_session(self, request: Any) -> Any:
        from aiohttp import web
        sid = request.match_info["sid"]
        return web.json_response({"session_id": sid, "messages": []})

    async def _run_command(self, request: Any) -> Any:
        """Execute a slash command."""
        from aiohttp import web
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        command = body.get("command", "")
        return web.json_response({"command": command, "status": "executed"})

    async def _list_tools(self, request: Any) -> Any:
        from aiohttp import web
        from ccb.tools import TOOL_REGISTRY
        tools = [{"name": t.name, "description": t.description} for t in TOOL_REGISTRY]
        return web.json_response({"tools": tools})

    async def _call_tool(self, request: Any) -> Any:
        """Execute a tool and return its result."""
        from aiohttp import web
        name = request.match_info["name"]
        try:
            body = await request.json()
        except Exception:
            body = {}

        try:
            from ccb.tools import TOOL_REGISTRY
            tool = next((t for t in TOOL_REGISTRY if t.name == name), None)
            if not tool:
                return web.json_response({"error": f"Unknown tool: {name}"}, status=404)
            result = await tool.run(body)
            return web.json_response({
                "tool": name,
                "result": result.content if hasattr(result, "content") else str(result),
                "is_error": getattr(result, "is_error", False),
            })
        except Exception as e:
            return web.json_response({"tool": name, "error": str(e)}, status=500)

    # ── WebSocket endpoint ──

    async def _websocket(self, request: Any) -> Any:
        """WebSocket endpoint for real-time bidirectional communication."""
        from aiohttp import web
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        ws_id = str(uuid.uuid4())[:8]
        self._ws_clients[ws_id] = ws

        try:
            async for msg in ws:
                if msg.type == 1:  # TEXT
                    try:
                        data = json.loads(msg.data)
                        method = data.get("method", "")
                        if method == "chat":
                            prompt = data.get("message", "")
                            try:
                                from ccb.query_engine import run_query
                                result = await run_query(prompt)
                                await ws.send_json({"type": "response", "content": result})
                            except Exception as e:
                                await ws.send_json({"type": "error", "error": str(e)})
                        elif method == "ping":
                            await ws.send_json({"type": "pong", "time": time.time()})
                        elif method == "subscribe":
                            await ws.send_json({"type": "subscribed", "channel": data.get("channel", "")})
                        else:
                            await ws.send_json({"type": "error", "error": f"Unknown method: {method}"})
                    except json.JSONDecodeError:
                        await ws.send_json({"type": "error", "error": "Invalid JSON"})
                elif msg.type == 258:  # ERROR
                    break
        finally:
            self._ws_clients.pop(ws_id, None)
        return ws

    async def broadcast(self, event: str, data: Any = None) -> int:
        """Broadcast an event to all connected WebSocket clients."""
        msg = json.dumps({"type": "event", "event": event, "data": data})
        sent = 0
        for ws in list(self._ws_clients.values()):
            try:
                await ws.send_str(msg)
                sent += 1
            except Exception:
                pass
        return sent

    # ── Auth middleware ──

    async def _auth_middleware(self, app: Any, handler: Any) -> Any:
        async def middleware_handler(request: Any) -> Any:
            from aiohttp import web
            # Skip auth for health endpoint
            if request.path == "/health":
                return await handler(request)
            auth = request.headers.get("Authorization", "")
            if self._api_key and not auth.endswith(self._api_key):
                return web.json_response({"error": "Unauthorized"}, status=401)
            return await handler(request)
        return middleware_handler

    def set_api_key(self, key: str) -> None:
        """Set an API key for authenticating requests."""
        self._api_key = key
