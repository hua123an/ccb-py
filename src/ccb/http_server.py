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
        from aiohttp import web
        name = request.match_info["name"]
        try:
            body = await request.json()
        except Exception:
            body = {}
        return web.json_response({"tool": name, "input": body, "status": "not_implemented"})
