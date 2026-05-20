"""HTTP API server mode for ccb-py.

Exposes a REST API so IDE extensions and other tools can interact
with ccb-py programmatically.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

from ccb.session import Session
from ccb.session_repository import list_sessions_with_active, load_serialized_session, save_session
from ccb.session_runtime import (
    emit_runtime_warning,
    resolve_session,
    run_session_turn,
    serialize_message,
)


class APIServer:
    """HTTP API server for ccb-py."""

    def __init__(self, host: str = "127.0.0.1", port: int = 3300):
        self.host = host
        self.port = port
        self._sessions: dict[str, Session] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._ws_clients: dict[str, Any] = {}
        self._api_key: str = ""
        self._running = False
        self._start_time = time.time()

    async def start(self) -> None:
        try:
            from aiohttp import web
        except ImportError:
            raise RuntimeError("aiohttp required: pip install aiohttp")

        app = web.Application(middlewares=[self._auth_middleware])
        app["ccb_api_server"] = self
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
        uptime_s = time.time() - self._start_time
        return web.json_response({
            "running": True,
            "sessions": len(self._sessions),
            "uptime": round(uptime_s, 1),
        })

    def _get_or_create_session(
        self,
        session_id: str | None = None,
        *,
        cwd: str | None = None,
        model: str | None = None,
    ) -> Session:
        """Resolve API session context, creating it on first use."""
        session = resolve_session(
            session_id,
            cwd=cwd,
            model=model,
            default_cwd=os.getcwd(),
            cache=self._sessions,
            create=True,
        )
        assert session is not None
        return session

    def _save_session(self, session: Session) -> None:
        """Persist the current API session state."""
        save_session(session)

    async def _chat(self, request: Any) -> Any:
        """Send a message and get a streaming response."""
        from aiohttp import web
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        prompt = body.get("message", "")
        session_id = body.get("session_id")

        if not prompt:
            return web.json_response({"error": "No message"}, status=400)

        try:
            from ccb.query_engine import run_query
            session, result = await run_session_turn(
                prompt,
                session_id=session_id,
                cwd=body.get("cwd") or None,
                model=body.get("model") or None,
                default_cwd=os.getcwd(),
                run_query=run_query,
                lock_store=self._session_locks,
                cache=self._sessions,
                save_session=self._save_session,
            )
            return web.json_response({
                "session_id": session.id,
                "cwd": session.cwd,
                "model": session.model,
                "total_input_tokens": session.total_input_tokens,
                "total_output_tokens": session.total_output_tokens,
                "last_input_tokens": session.last_input_tokens,
                "response": result,
                "messages": [serialize_message(m) for m in session.messages],
            })
        except Exception as e:
            emit_runtime_warning(
                "http_chat_failed",
                session_id=(session_id or ""),
                cwd=body.get("cwd") or os.getcwd(),
                payload={"error": str(e)},
            )
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
            result = await run_query(prompt, model=model, system_prompt=system, cwd=os.getcwd())
            return web.json_response({"result": result})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _list_sessions(self, request: Any) -> Any:
        from aiohttp import web
        sessions = list_sessions_with_active(active_sessions=self._sessions)
        return web.json_response({"sessions": sessions})

    async def _get_session(self, request: Any) -> Any:
        from aiohttp import web
        sid = request.match_info["sid"]
        serialized = load_serialized_session(sid, active_sessions=self._sessions)
        if serialized is not None:
            if sid not in self._sessions:
                session = resolve_session(
                    sid,
                    default_cwd=os.getcwd(),
                    cache=self._sessions,
                    create=False,
                )
                if session is not None:
                    self._sessions[sid] = session
            return web.json_response(serialized)
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
        from ccb.tools.base import create_default_registry
        import os
        registry = create_default_registry(os.getcwd())
        tools = [{"name": t.name, "description": t.description} for t in registry.all_tools()]
        return web.json_response({"tools": tools})

    async def _call_tool(self, request: Any) -> Any:
        """Execute a tool and return its result."""
        from aiohttp import web
        import os
        name = request.match_info["name"]
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"tool": name, "error": "Invalid JSON"}, status=400)

        try:
            from ccb.tools.base import create_default_registry, validate_input
            registry = create_default_registry(os.getcwd())
            tool = registry.get(name)
            if not tool:
                return web.json_response({"error": f"Unknown tool: {name}"}, status=404)
            validation_errors = validate_input(body, tool.input_schema)
            if validation_errors:
                return web.json_response(
                    {
                        "tool": name,
                        "error": "Invalid tool input: " + "; ".join(validation_errors),
                    },
                    status=400,
                )
            result = await tool.execute(body, cwd=os.getcwd())
            return web.json_response({
                "tool": name,
                "result": result.output if hasattr(result, "output") else str(result),
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
                                session_id = data.get("session_id")
                                session, result = await run_session_turn(
                                    prompt,
                                    session_id=session_id,
                                    cwd=data.get("cwd") or None,
                                    model=data.get("model") or None,
                                    default_cwd=os.getcwd(),
                                    run_query=run_query,
                                    lock_store=self._session_locks,
                                    cache=self._sessions,
                                    save_session=self._save_session,
                                )
                                await ws.send_json({
                                    "type": "response",
                                    "session_id": session.id,
                                    "cwd": session.cwd,
                                    "model": session.model,
                                    "total_input_tokens": session.total_input_tokens,
                                    "total_output_tokens": session.total_output_tokens,
                                    "last_input_tokens": session.last_input_tokens,
                                    "content": result,
                                    "messages": [serialize_message(m) for m in session.messages],
                                })
                            except Exception as e:
                                emit_runtime_warning(
                                    "http_websocket_chat_failed",
                                    session_id=(data.get("session_id") or ""),
                                    cwd=data.get("cwd") or os.getcwd(),
                                    payload={"error": str(e)},
                                )
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

    @staticmethod
    async def _auth_middleware(request: Any, handler: Any) -> Any:
        from aiohttp import web
        server = request.app.get("ccb_api_server")
        if request.path == "/health" or not server or not server._api_key:
            return await handler(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != server._api_key:
            return web.json_response({"error": "Unauthorized"}, status=401)
        return await handler(request)

    def set_api_key(self, key: str) -> None:
        """Set an API key for authenticating requests."""
        self._api_key = key
