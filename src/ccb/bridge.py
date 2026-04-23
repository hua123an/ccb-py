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
        self._pending_requests: dict[str, asyncio.Future] = {}
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

                        # Handle responses to our requests
                        if data.get("type") == "response":
                            req_id = data.get("id")
                            if req_id and req_id in self._pending_requests:
                                fut = self._pending_requests.pop(req_id)
                                if not fut.done():
                                    if "error" in data:
                                        fut.set_exception(Exception(data["error"]))
                                    else:
                                        fut.set_result(data.get("result"))
                            continue

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


    # ── Request to IDE ──

    async def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> Any:
        """Send a request to the IDE and wait for a response."""
        if not self._connections:
            return None
        import uuid
        req_id = str(uuid.uuid4())[:8]
        msg = json.dumps({
            "type": "request",
            "id": req_id,
            "method": method,
            "params": params or {},
        })

        # Set up response future
        loop = asyncio.get_event_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending_requests[req_id] = future

        ws = self._connections[0]  # Send to first connected client
        try:
            await ws.send(msg)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            return None

    async def get_editor_state(self) -> dict[str, Any]:
        """Request current editor state from IDE."""
        result = await self.request("editor/getState")
        return result if isinstance(result, dict) else {}

    async def get_workspace_folders(self) -> list[str]:
        result = await self.request("workspace/getFolders")
        return result if isinstance(result, list) else []

    async def get_selection(self) -> dict[str, Any]:
        """Get current text selection in the IDE."""
        result = await self.request("editor/getSelection")
        return result if isinstance(result, dict) else {}

    async def insert_text(self, text: str, position: dict[str, int] | None = None) -> bool:
        result = await self.request("editor/insertText", {"text": text, "position": position})
        return bool(result)

    async def show_message(self, message: str, severity: str = "info") -> None:
        await self.notify("window/showMessage", {"message": message, "severity": severity})

    async def get_terminal_output(self) -> str:
        result = await self.request("terminal/getOutput")
        return result if isinstance(result, str) else ""

    # ── VS Code specific protocol support ──

    async def vscode_execute_command(self, command: str, args: list[Any] | None = None) -> Any:
        """Execute a VS Code command."""
        return await self.request("vscode/executeCommand", {"command": command, "args": args or []})

    async def vscode_open_settings(self, query: str = "") -> None:
        await self.request("vscode/openSettings", {"query": query})

    async def vscode_show_quick_pick(self, items: list[str], placeholder: str = "") -> str | None:
        result = await self.request("vscode/showQuickPick", {
            "items": items, "placeholder": placeholder
        })
        return result if isinstance(result, str) else None

    async def vscode_show_input_box(self, prompt: str = "", value: str = "") -> str | None:
        result = await self.request("vscode/showInputBox", {"prompt": prompt, "value": value})
        return result if isinstance(result, str) else None

    # ── JetBrains protocol support ──

    async def jetbrains_run_action(self, action_id: str) -> Any:
        return await self.request("jetbrains/runAction", {"actionId": action_id})

    # ── File watch ──

    async def watch_file(self, pattern: str) -> bool:
        result = await self.request("workspace/watchFile", {"pattern": pattern})
        return bool(result)

    async def unwatch_file(self, pattern: str) -> bool:
        result = await self.request("workspace/unwatchFile", {"pattern": pattern})
        return bool(result)

    # ── Diagnostics push ──

    async def push_diagnostics(self, path: str, diagnostics: list[dict[str, Any]]) -> None:
        await self.notify("textDocument/publishDiagnostics", {
            "uri": f"file://{path}",
            "diagnostics": diagnostics,
        })

    # ── Inline completions ──

    async def provide_inline_completion(self, path: str, line: int, column: int, text: str) -> None:
        await self.notify("editor/inlineCompletion", {
            "path": path, "line": line, "column": column, "text": text,
        })


# Module singleton
_bridge: IDEBridge | None = None


def get_bridge() -> IDEBridge:
    global _bridge
    if _bridge is None:
        _bridge = IDEBridge()
    return _bridge
