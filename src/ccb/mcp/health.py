"""MCP server health checking and auto-reconnection.

Monitors connected MCP servers and handles:
- Periodic health pings
- Automatic reconnection on failure
- Connection state tracking
- Graceful degradation
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ccb.mcp.client import MCPManager, MCPServer


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass
class ServerHealth:
    name: str
    state: ConnectionState = ConnectionState.DISCONNECTED
    last_ping: float = 0.0
    last_pong: float = 0.0
    ping_latency_ms: float = 0.0
    reconnect_count: int = 0
    consecutive_failures: int = 0
    last_error: str = ""
    tools_count: int = 0


class HealthMonitor:
    """Monitors MCP server health and manages reconnection."""

    def __init__(
        self,
        manager: MCPManager,
        ping_interval: float = 30.0,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
    ):
        self._manager = manager
        self._ping_interval = ping_interval
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._health: dict[str, ServerHealth] = {}
        self._monitor_task: asyncio.Task[None] | None = None
        self._running = False
        self._configs: dict[str, dict[str, Any]] = {}  # Stored configs for reconnection

    def register_server(self, name: str, config: dict[str, Any]) -> None:
        """Register a server for health monitoring."""
        self._configs[name] = config
        self._health[name] = ServerHealth(
            name=name,
            state=ConnectionState.CONNECTED,
            tools_count=len(self._manager.servers.get(name, type("", (), {"tools": []})()).tools)
            if hasattr(self._manager.servers.get(name), 'tools') else 0,
        )

    def register_connected(self, name: str, config: dict[str, Any]) -> None:
        """Register a server that's already connected."""
        self._configs[name] = config
        server = self._manager.servers.get(name)
        self._health[name] = ServerHealth(
            name=name,
            state=ConnectionState.CONNECTED,
            tools_count=len(server.tools) if server else 0,
        )

    async def start(self) -> None:
        """Start the health monitoring loop."""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self) -> None:
        while self._running:
            for name in list(self._health.keys()):
                await self._check_server(name)
            await asyncio.sleep(self._ping_interval)

    async def _check_server(self, name: str) -> None:
        """Ping a server and reconnect if needed."""
        health = self._health.get(name)
        if not health:
            return

        server = self._manager.servers.get(name)
        if not server or not server.connected:
            if health.state != ConnectionState.FAILED:
                health.state = ConnectionState.DISCONNECTED
                await self._try_reconnect(name)
            return

        # Send ping
        health.last_ping = time.time()
        try:
            await asyncio.wait_for(
                self._manager._send_request(server, "ping", {}),
                timeout=10,
            )
            health.last_pong = time.time()
            health.ping_latency_ms = (health.last_pong - health.last_ping) * 1000
            health.consecutive_failures = 0
            health.state = ConnectionState.CONNECTED
        except Exception as e:
            health.consecutive_failures += 1
            health.last_error = str(e)

            if health.consecutive_failures >= self._max_retries:
                health.state = ConnectionState.FAILED
            else:
                await self._try_reconnect(name)

    async def _try_reconnect(self, name: str) -> bool:
        """Attempt to reconnect a server with exponential backoff."""
        health = self._health.get(name)
        config = self._configs.get(name)
        if not health or not config:
            return False

        if health.reconnect_count >= self._max_retries:
            health.state = ConnectionState.FAILED
            return False

        health.state = ConnectionState.RECONNECTING
        health.reconnect_count += 1

        # Exponential backoff
        delay = min(
            self._backoff_base * (2 ** (health.reconnect_count - 1)),
            self._backoff_max,
        )
        await asyncio.sleep(delay)

        try:
            # Disconnect old
            server = self._manager.servers.get(name)
            if server:
                if server._read_task:
                    server._read_task.cancel()
                if server._proc:
                    try:
                        server._proc.terminate()
                    except ProcessLookupError:
                        pass
                del self._manager._servers[name]

            # Reconnect
            await self._manager.connect(name, config)
            health.state = ConnectionState.CONNECTED
            health.consecutive_failures = 0
            new_server = self._manager.servers.get(name)
            health.tools_count = len(new_server.tools) if new_server else 0
            return True

        except Exception as e:
            health.last_error = str(e)
            if health.reconnect_count >= self._max_retries:
                health.state = ConnectionState.FAILED
            return False

    # ── Public API ──

    def get_health(self, name: str) -> ServerHealth | None:
        return self._health.get(name)

    def get_all_health(self) -> dict[str, ServerHealth]:
        return dict(self._health)

    def is_healthy(self, name: str) -> bool:
        h = self._health.get(name)
        return h is not None and h.state == ConnectionState.CONNECTED

    def summary(self) -> dict[str, Any]:
        total = len(self._health)
        connected = sum(1 for h in self._health.values() if h.state == ConnectionState.CONNECTED)
        failed = sum(1 for h in self._health.values() if h.state == ConnectionState.FAILED)
        return {
            "total": total,
            "connected": connected,
            "disconnected": total - connected - failed,
            "failed": failed,
            "servers": {
                name: {
                    "state": h.state.value,
                    "latency_ms": round(h.ping_latency_ms, 1),
                    "reconnects": h.reconnect_count,
                    "tools": h.tools_count,
                    "error": h.last_error if h.state == ConnectionState.FAILED else "",
                }
                for name, h in self._health.items()
            },
        }

    async def force_reconnect(self, name: str) -> bool:
        """Force immediate reconnection of a server."""
        health = self._health.get(name)
        if health:
            health.reconnect_count = 0
            health.state = ConnectionState.DISCONNECTED
            return await self._try_reconnect(name)
        return False
