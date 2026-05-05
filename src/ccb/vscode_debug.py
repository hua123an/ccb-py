"""VS Code Debug Integration for ccb-py.

Provides a WebSocket-based debug server implementing a subset of the
Chrome DevTools Protocol (CDP) for VS Code attach-mode debugging.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Breakpoint:
    """A registered breakpoint."""
    id: str
    file: str
    line: int
    condition: str = ""
    hit_count: int = 0
    enabled: bool = True


@dataclass
class StackFrame:
    """A debug stack frame."""
    id: str
    name: str
    file: str
    line: int
    column: int = 0


@dataclass
class Variable:
    """A variable in the current debug scope."""
    name: str
    value: str
    type: str = "string"
    variables_reference: int = 0


@dataclass
class DebugState:
    """Current state of the debugger."""
    paused: bool = False
    current_file: str = ""
    current_line: int = 0
    call_stack: list[StackFrame] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    breakpoints: dict[str, list[Breakpoint]] = field(default_factory=dict)


class VSCodeDebugServer:
    """WebSocket debug server for VS Code attach-mode debugging.

    Implements a subset of the Chrome DevTools Protocol (CDP) that VS Code
    understands, allowing live inspection of ccb-py execution.

    Args:
        host: Bind address (default ``127.0.0.1``).
        port: Bind port (default ``9333``).
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9333):
        self.host = host
        self.port = port
        self._state = DebugState()
        self._ws_clients: dict[str, Any] = {}
        self._running = False
        self._start_time = time.time()
        self._event_log: list[dict[str, Any]] = []
        self._step_count = 0
        self._eval_scope: dict[str, Any] = {}

    # ── Lifecycle ─────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the WebSocket debug server."""
        try:
            from aiohttp import web
        except ImportError:
            raise RuntimeError("aiohttp required: pip install aiohttp")

        app = web.Application()
        app.router.add_get("/", self._handle_ws)
        app.router.add_get("/json", self._handle_descriptor)
        app.router.add_get("/json/version", self._handle_version)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        self._running = True
        print(f"ccb-py Debug Server: ws://{self.host}:{self.port}")

    def stop(self) -> None:
        """Stop the debug server."""
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def summary(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "host": self.host,
            "port": self.port,
            "clients": len(self._ws_clients),
            "breakpoints": sum(len(bps) for bps in self._state.breakpoints.values()),
            "paused": self._state.paused,
            "step_count": self._step_count,
        }

    # ── Debug event emission ─────────────────────────────────────

    async def send_event(self, method: str, params: dict[str, Any] | None = None) -> int:
        """Push a debug event to all connected VS Code clients.

        Args:
            method: CDP method name (e.g. ``Debugger.paused``).
            params: Event parameters.

        Returns:
            Number of clients the event was sent to.
        """
        event = {
            "method": method,
            "params": params or {},
        }
        self._event_log.append({"time": time.time(), "method": method, "params": params})
        msg = json.dumps(event)
        sent = 0
        for ws in list(self._ws_clients.values()):
            try:
                await ws.send_str(msg)
                sent += 1
            except Exception:
                pass
        return sent

    def handle_breakpoint(self, line: int, file: str, condition: str = "") -> Breakpoint:
        """Register a breakpoint.

        Args:
            line: 1-based line number.
            file: Source file path.
            condition: Optional condition expression.

        Returns:
            The created :class:`Breakpoint` object.
        """
        bp = Breakpoint(
            id=str(uuid.uuid4())[:8],
            file=file,
            line=line,
            condition=condition,
        )
        self._state.breakpoints.setdefault(file, []).append(bp)
        return bp

    def remove_breakpoint(self, bp_id: str) -> bool:
        """Remove a breakpoint by ID. Returns True if found."""
        for file, bps in self._state.breakpoints.items():
            for i, bp in enumerate(bps):
                if bp.id == bp_id:
                    bps.pop(i)
                    if not bps:
                        del self._state.breakpoints[file]
                    return True
        return False

    def get_breakpoints(self, file: str | None = None) -> list[Breakpoint]:
        """Get breakpoints, optionally filtered by file."""
        if file:
            return list(self._state.breakpoints.get(file, []))
        result = []
        for bps in self._state.breakpoints.values():
            result.extend(bps)
        return result

    def check_breakpoint(self, file: str, line: int) -> Breakpoint | None:
        """Check if execution should pause at this location.

        Returns the matching :class:`Breakpoint` if hit, else None.
        """
        for bp in self._state.breakpoints.get(file, []):
            if bp.enabled and bp.line == line:
                bp.hit_count += 1
                if bp.condition:
                    # Evaluate condition in current scope
                    try:
                        if not eval(bp.condition, {"__builtins__": {}}, self._eval_scope):
                            continue
                    except Exception:
                        continue
                return bp
        return None

    async def handle_step(self, file: str, line: int, scope_vars: dict[str, Any] | None = None) -> bool:
        """Called at each execution step.

        Checks breakpoints, pauses if hit, and emits ``Debugger.scriptParsed``
        and step events.

        Args:
            file: Current source file.
            line: Current line number.
            scope_vars: Variables in current scope.

        Returns:
            True if execution should pause (breakpoint hit).
        """
        self._step_count += 1
        self._state.current_file = file
        self._state.current_line = line
        if scope_vars:
            self._eval_scope.update(scope_vars)
            self._state.variables = scope_vars

        bp = self.check_breakpoint(file, line)
        if bp:
            self._state.paused = True
            frame_id = str(uuid.uuid4())[:8]
            self._state.call_stack = [
                StackFrame(id=frame_id, name="<ccb>", file=file, line=line)
            ]
            await self.send_event("Debugger.paused", {
                "reason": "breakpoint",
                "hitBreakpoints": [bp.id],
                "callFrames": [{
                    "callFrameId": frame_id,
                    "functionName": "<ccb>",
                    "location": {"scriptId": file, "lineNumber": line},
                    "scopeChain": [{
                        "type": "local",
                        "object": {"type": "object", "description": "Local"},
                    }],
                }],
            })
            return True

        # Emit step event for live tracing
        await self.send_event("Debugger.resumed", {})
        return False

    def get_variables(self, frame_id: str | None = None) -> list[Variable]:
        """Inspect variables in the current (or specified) frame.

        Args:
            frame_id: Optional frame ID (currently only one frame supported).

        Returns:
            List of :class:`Variable` objects.
        """
        result = []
        for name, val in self._state.variables.items():
            vtype = type(val).__name__
            result.append(Variable(
                name=name,
                value=str(val)[:500],
                type=vtype,
            ))
        return result

    def evaluate(self, expression: str) -> dict[str, Any]:
        """Evaluate an expression in the current debug scope.

        Args:
            expression: Python expression to evaluate.

        Returns:
            Dict with ``result`` and ``type`` keys, or ``error`` on failure.
        """
        try:
            result = eval(expression, {"__builtins__": __builtins__}, self._eval_scope)
            return {
                "result": str(result)[:2000],
                "type": type(result).__name__,
            }
        except Exception as e:
            return {"error": str(e), "type": "error"}

    def set_variable(self, name: str, value: Any) -> None:
        """Set a variable in the debug scope."""
        self._eval_scope[name] = value
        self._state.variables[name] = value

    def get_call_stack(self) -> list[dict[str, Any]]:
        """Get the current call stack."""
        return [
            {"id": f.id, "name": f.name, "file": f.file, "line": f.line, "column": f.column}
            for f in self._state.call_stack
        ]

    def get_event_log(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent debug events."""
        return self._event_log[-limit:]

    # ── Integration helpers ───────────────────────────────────────

    async def emit_tool_start(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """Emit an event when a tool call begins (for loop.py integration)."""
        await self.send_event("Debugger.scriptParsed", {
            "scriptId": f"tool:{tool_name}",
            "url": f"ccb://tool/{tool_name}",
        })
        await self.send_event("Debugger.paused", {
            "reason": "step",
            "callFrames": [{
                "callFrameId": f"tool-{self._step_count}",
                "functionName": tool_name,
                "location": {"scriptId": f"tool:{tool_name}", "lineNumber": 0},
                "scopeChain": [{
                    "type": "local",
                    "object": {"type": "object", "description": "Tool Input"},
                }],
            }],
        })

    async def emit_tool_end(self, tool_name: str, result: str, is_error: bool = False) -> None:
        """Emit an event when a tool call completes."""
        await self.send_event("Debugger.resumed", {})
        await self.send_event("Runtime.consoleAPICalled", {
            "type": "log" if not is_error else "error",
            "args": [{"type": "string", "value": f"[{tool_name}] {result[:500]}"}],
        })

    # ── VS Code / CDP handlers ────────────────────────────────────

    async def _handle_ws(self, request: Any) -> Any:
        from aiohttp import web
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        ws_id = str(uuid.uuid4())[:8]
        self._ws_clients[ws_id] = ws

        # Send initial Debugger.enable-style response
        await ws.send_json({
            "method": "Debugger.scriptParsed",
            "params": {
                "scriptId": "ccb-main",
                "url": "ccb://main",
                "startLine": 0,
                "startColumn": 0,
                "endLine": 9999,
                "endColumn": 0,
            },
        })

        try:
            async for msg in ws:
                if msg.type == 1:  # TEXT
                    try:
                        data = json.loads(msg.data)
                        response = await self._handle_cdp_message(data)
                        if response is not None:
                            await ws.send_json(response)
                    except json.JSONDecodeError:
                        await ws.send_json({"error": {"code": -32700, "message": "Parse error"}})
                elif msg.type == 258:  # ERROR
                    break
        finally:
            self._ws_clients.pop(ws_id, None)
        return ws

    async def _handle_cdp_message(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a CDP message from VS Code."""
        method = data.get("method", "")
        params = data.get("params", {})
        msg_id = data.get("id")

        result: dict[str, Any] | None = None

        if method == "Debugger.enable":
            result = {}
        elif method == "Debugger.disable":
            self._state.paused = False
            result = {}
        elif method == "Debugger.setBreakpoints":
            file = params.get("location", {}).get("scriptId", "")
            lines = [bp.get("lineNumber", 0) for bp in params.get("breakpoints", [])]
            # Clear existing breakpoints for this file
            self._state.breakpoints.pop(file, None)
            created = []
            for line in lines:
                bp = self.handle_breakpoint(line, file)
                created.append({"id": bp.id, "lineNumber": line})
            result = {"breakpoints": created}
        elif method == "Debugger.resume":
            self._state.paused = False
            result = {}
        elif method == "Debugger.stepOver" or method == "Debugger.stepInto":
            self._state.paused = False
            result = {}
        elif method == "Debugger.getScriptSource":
            result = {"scriptSource": f"// ccb-py debug target\n// {self._state.current_file}:{self._state.current_line}"}
        elif method == "Runtime.evaluate":
            expr = params.get("expression", "")
            eval_result = self.evaluate(expr)
            result = {"result": {"type": "string", "value": eval_result.get("result", eval_result.get("error", ""))}}
        elif method == "Debugger.evaluateOnCallFrame":
            expr = params.get("expression", "")
            eval_result = self.evaluate(expr)
            result = {"result": {"type": "string", "value": eval_result.get("result", eval_result.get("error", ""))}}
        elif method == "Runtime.enable":
            result = {}

        if msg_id is not None and result is not None:
            return {"id": msg_id, "result": result}
        return None

    async def _handle_descriptor(self, request: Any) -> Any:
        """Return Chrome-style debug target descriptor."""
        from aiohttp import web
        return web.json_response([{
            "description": "ccb-py debug target",
            "devtoolsFrontendUrl": f"devtools://devtools/bundled/inspector.html?ws={self.host}:{self.port}",
            "id": "ccb-py",
            "title": "ccb-py",
            "type": "node",
            "url": f"ws://{self.host}:{self.port}",
            "webSocketDebuggerUrl": f"ws://{self.host}:{self.port}",
        }])

    async def _handle_version(self, request: Any) -> Any:
        from aiohttp import web
        return web.json_response({
            "Browser": "ccb-py/1.0",
            "Protocol-Version": "1.3",
            "User-Agent": "ccb-py-debug/1.0",
            "V8-Version": "N/A",
            "WebKit-Version": "N/A",
        })


# ── launch.json generation ───────────────────────────────────────

def generate_launch_json(project_dir: str = ".vscode") -> Path:
    """Generate a ``.vscode/launch.json`` with attach configuration.

    Args:
        project_dir: Directory containing the ``.vscode`` folder.

    Returns:
        Path to the written ``launch.json``.
    """
    vscode_dir = Path(project_dir)
    vscode_dir.mkdir(parents=True, exist_ok=True)
    launch_path = vscode_dir / "launch.json"

    config = {
        "version": "0.2.0",
        "configurations": [
            {
                "name": "Attach to ccb-py",
                "type": "node",
                "request": "attach",
                "port": 9333,
                "websocketAddress": "ws://127.0.0.1:9333",
                "skipFiles": ["<node_internals>/**"],
                "sourceMaps": False,
                "cwd": "${workspaceFolder}",
            },
            {
                "name": "Attach to ccb-py (custom port)",
                "type": "node",
                "request": "attach",
                "port": "${input:ccbDebugPort}",
                "websocketAddress": "ws://127.0.0.1:${input:ccbDebugPort}",
                "skipFiles": ["<node_internals>/**"],
                "sourceMaps": False,
                "cwd": "${workspaceFolder}",
            },
        ],
        "inputs": [
            {
                "id": "ccbDebugPort",
                "type": "promptString",
                "description": "ccb-py debug server port",
                "default": "9333",
            }
        ],
    }

    launch_path.write_text(json.dumps(config, indent=2) + "\n")
    return launch_path


# Convenience: module-level singleton
_debug_server: VSCodeDebugServer | None = None


def get_debug_server(**kwargs: Any) -> VSCodeDebugServer:
    """Get or create the module-level VSCodeDebugServer singleton."""
    global _debug_server
    if _debug_server is None:
        _debug_server = VSCodeDebugServer(**kwargs)
    return _debug_server
