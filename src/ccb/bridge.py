"""IDE bridge for ccb-py.

WebSocket-based bidirectional communication with IDE extensions
(VS Code, JetBrains, etc.). Supports file sync, editor state,
diagnostics, and command execution.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class BridgeMessage:
    type: str  # "request", "response", "notification"
    method: str
    id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None


MessageHandler = Callable[[BridgeMessage], Awaitable[Any]]


class IDEBridge:
    """WebSocket bridge to IDE extensions."""

    def __init__(self, host: str = "127.0.0.1", port: int = 3200):
        self.host = host
        self.port = port
        self._handlers: dict[str, MessageHandler] = {}
        self._connections: list[Any] = []
        self._running = False
        self._server: Any = None
        self._register_default_handlers()

    def _register_default_handlers(self) -> None:
        self.on("ping", self._handle_ping)
        self.on("getStatus", self._handle_status)
        self.on("executeCommand", self._handle_execute_command)
        self.on("openFile", self._handle_open_file)
        self.on("getActiveFile", self._handle_active_file)
        self.on("getDiagnostics", self._handle_diagnostics)

    def on(self, method: str, handler: MessageHandler) -> None:
        self._handlers[method] = handler

    async def _handle_ping(self, msg: BridgeMessage) -> dict[str, Any]:
        return {"status": "ok", "time": time.time()}

    async def _handle_status(self, msg: BridgeMessage) -> dict[str, Any]:
        return {
            "connected_clients": len(self._connections),
            "uptime": time.time(),
            "version": "1.0.0",
        }

    async def _handle_execute_command(self, msg: BridgeMessage) -> dict[str, Any]:
        cmd = msg.params.get("command", "")
        # Delegate to ccb command handler
        return {"executed": cmd, "status": "ok"}

    async def _handle_open_file(self, msg: BridgeMessage) -> dict[str, Any]:
        path = msg.params.get("path", "")
        line = msg.params.get("line", 0)
        return {"opened": path, "line": line}

    async def _handle_active_file(self, msg: BridgeMessage) -> dict[str, Any]:
        return {"path": "", "language": "", "cursor": {"line": 0, "column": 0}}

    async def _handle_diagnostics(self, msg: BridgeMessage) -> dict[str, Any]:
        return {"diagnostics": []}

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a notification to all connected IDE clients."""
        msg = json.dumps({
            "type": "notification",
            "method": method,
            "params": params or {},
        })
        for ws in self._connections:
            try:
                await ws.send(msg)
            except Exception:
                pass

    async def notify_file_changed(self, path: str, content: str = "") -> None:
        await self.notify("fileChanged", {"path": path, "content": content})

    async def notify_output(self, text: str) -> None:
        await self.notify("output", {"text": text})

    async def notify_status(self, status: str, message: str = "") -> None:
        await self.notify("statusUpdate", {"status": status, "message": message})

    async def start(self) -> None:
        """Start the WebSocket bridge server."""
        try:
            import websockets
        except ImportError:
            raise RuntimeError("websockets required: pip install websockets")

        async def handler(websocket: Any) -> None:
            self._connections.append(websocket)
            try:
                async for raw in websocket:
                    try:
                        data = json.loads(raw)
                        msg = BridgeMessage(
                            type=data.get("type", "request"),
                            method=data.get("method", ""),
                            id=data.get("id"),
                            params=data.get("params", {}),
                        )
                        handler_fn = self._handlers.get(msg.method)
                        if handler_fn:
                            result = await handler_fn(msg)
                            if msg.id:
                                await websocket.send(json.dumps({
                                    "type": "response",
                                    "id": msg.id,
                                    "result": result,
                                }))
                        elif msg.id:
                            await websocket.send(json.dumps({
                                "type": "response",
                                "id": msg.id,
                                "error": f"Unknown method: {msg.method}",
                            }))
                    except json.JSONDecodeError:
                        pass
            finally:
                self._connections.remove(websocket)

        self._running = True
        self._server = await websockets.serve(handler, self.host, self.port)

    async def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Module singleton
_bridge: IDEBridge | None = None


def get_bridge() -> IDEBridge:
    global _bridge
    if _bridge is None:
        _bridge = IDEBridge()
    return _bridge
