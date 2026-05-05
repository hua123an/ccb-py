"""Zero-config LAN peer discovery using UDP broadcast.

Allows multiple ccb-py instances on the same network to find each
other automatically without manual configuration.
"""
from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from typing import Any

# ── Constants ──
DEFAULT_PORT = 9876
BROADCAST_INTERVAL = 5.0      # seconds between presence broadcasts
PEER_STALE_TIMEOUT = 30.0     # seconds before a peer is considered stale
PEER_DEAD_TIMEOUT = 120.0     # seconds before a peer is removed entirely


def _get_local_ip() -> str:
    """Return the LAN IP of this machine (best-effort)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class PeerInfo:
    """Information about a discovered peer."""

    __slots__ = ("id", "host", "port", "model", "status", "last_seen", "task")

    def __init__(
        self,
        id: str,
        host: str,
        port: int,
        model: str = "",
        status: str = "active",
        last_seen: float | None = None,
        task: str = "",
    ) -> None:
        self.id = id
        self.host = host
        self.port = port
        self.model = model
        self.status = status
        self.last_seen = last_seen or time.time()
        self.task = task

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "host": self.host,
            "port": self.port,
            "model": self.model,
            "status": self.status,
            "last_seen": self.last_seen,
            "task": self.task,
        }

    def is_stale(self) -> bool:
        return (time.time() - self.last_seen) > PEER_STALE_TIMEOUT

    def is_dead(self) -> bool:
        return (time.time() - self.last_seen) > PEER_DEAD_TIMEOUT

    def refresh(self, model: str = "", status: str = "", task: str = "") -> None:
        self.last_seen = time.time()
        if model:
            self.model = model
        if status:
            self.status = status
        if task:
            self.task = task


class PeerDiscovery:
    """UDP broadcast-based LAN peer discovery.

    Each instance periodically broadcasts its presence and listens for
    broadcasts from others.  Peers transition through:
        discovered -> active -> stale (30 s) -> removed (120 s)
    """

    def __init__(
        self,
        instance_id: str | None = None,
        port: int = DEFAULT_PORT,
        model: str = "",
    ) -> None:
        self.instance_id = instance_id or f"ccb-{uuid.uuid4().hex[:8]}"
        self.port = port
        self.model = model
        self.local_ip = _get_local_ip()
        self._peers: dict[str, PeerInfo] = {}
        self._running = False
        self._broadcast_task: asyncio.Task[None] | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._tcp_port: int = 0  # set externally if TCP message routing is used

    # ── Lifecycle ──

    async def start(self) -> None:
        """Start broadcasting and listening."""
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        # Small delay so the listener socket is bound before we broadcast
        await asyncio.sleep(0.1)
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        """Stop all background tasks."""
        self._running = False
        for task in (self._broadcast_task, self._listen_task, self._cleanup_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    # ── Public API ──

    def get_peers(self, include_stale: bool = False) -> list[dict[str, Any]]:
        """Return list of known peers.

        Args:
            include_stale: If True, include peers that haven't been heard
                from in the last 30 seconds.
        """
        self._prune_dead()
        result: list[dict[str, Any]] = []
        for peer in self._peers.values():
            if not include_stale and peer.is_stale():
                continue
            result.append(peer.to_dict())
        return result

    def get_peer(self, peer_id: str) -> PeerInfo | None:
        peer = self._peers.get(peer_id)
        if peer and not peer.is_dead():
            return peer
        return None

    def set_current_task(self, task: str) -> None:
        """Update the task description that gets broadcast."""
        self._current_task = task

    async def send_to_peer(
        self,
        peer_id: str,
        msg_type: str,
        payload: dict[str, Any],
    ) -> bool:
        """Send a TCP message to a specific peer.

        Returns True on success.
        """
        peer = self._peers.get(peer_id)
        if not peer or peer.is_stale():
            return False
        envelope = json.dumps({
            "sender_id": self.instance_id,
            "msg_type": msg_type,
            "payload": payload,
            "timestamp": time.time(),
        }, ensure_ascii=False)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(peer.host, peer.port),
                timeout=5.0,
            )
            writer.write(envelope.encode("utf-8") + b"\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    # ── Background loops ──

    async def _broadcast_loop(self) -> None:
        """Periodically broadcast our presence via UDP."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        loop = asyncio.get_event_loop()

        def _send() -> None:
            try:
                sock.sendto(packet, ("255.255.255.255", self.port))
            except OSError:
                pass

        while self._running:
            try:
                packet = self._make_presence_packet()
                await loop.run_in_executor(None, _send)
            except OSError:
                pass
            await asyncio.sleep(BROADCAST_INTERVAL)

        sock.close()

    async def _listen_loop(self) -> None:
        """Listen for UDP presence broadcasts from other instances."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        sock.bind(("", self.port))
        sock.setblocking(False)
        loop = asyncio.get_event_loop()

        def _recv() -> tuple[bytes, tuple[str, int]] | None:
            try:
                return sock.recvfrom(65536)
            except (OSError, BlockingIOError):
                return None

        while self._running:
            try:
                result = await loop.run_in_executor(None, _recv)
                if result is not None:
                    data, addr = result
                    self._handle_broadcast(data, addr)
            except asyncio.CancelledError:
                break
            except OSError:
                await asyncio.sleep(0.1)

        sock.close()

    async def _cleanup_loop(self) -> None:
        """Periodically remove dead peers."""
        while self._running:
            await asyncio.sleep(10)
            self._prune_dead()

    # ── Packet handling ──

    def _make_presence_packet(self) -> bytes:
        payload = {
            "id": self.instance_id,
            "host": self.local_ip,
            "port": self.port,
            "model": self.model,
            "status": "active",
            "tcp_port": self._tcp_port,
            "task": getattr(self, "_current_task", ""),
            "ts": time.time(),
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _handle_broadcast(self, data: bytes, addr: tuple[str, int]) -> None:
        """Process an incoming UDP broadcast."""
        try:
            info = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        peer_id = info.get("id", "")
        if not peer_id or peer_id == self.instance_id:
            return  # ignore our own broadcasts

        host = info.get("host", addr[0])
        port = info.get("port", self.port)
        model = info.get("model", "")
        status = info.get("status", "active")
        task = info.get("task", "")

        existing = self._peers.get(peer_id)
        if existing:
            existing.refresh(model=model, status=status, task=task)
        else:
            self._peers[peer_id] = PeerInfo(
                id=peer_id,
                host=host,
                port=port,
                model=model,
                status=status,
                task=task,
            )

    def _prune_dead(self) -> None:
        """Remove peers that haven't been heard from."""
        dead = [pid for pid, p in self._peers.items() if p.is_dead()]
        for pid in dead:
            del self._peers[pid]
