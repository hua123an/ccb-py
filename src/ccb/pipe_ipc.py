"""Named-pipe based same-machine multi-agent orchestration.

Provides PipeIPC for local inter-process communication between
multiple ccb-py instances on the same machine.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# ── Platform-aware pipe directory ──
_PIPE_DIR = Path.home() / ".claude" / "ccb-pipes"

# Message types
MSG_TASK_ASSIGN = "task_assign"
MSG_TASK_RESULT = "task_result"
MSG_HEARTBEAT = "heartbeat"
MSG_DISCOVER = "discover"

VALID_MSG_TYPES = frozenset({
    MSG_TASK_ASSIGN,
    MSG_TASK_RESULT,
    MSG_HEARTBEAT,
    MSG_DISCOVER,
})

_IS_WINDOWS = sys.platform == "win32"


def _pipe_path_for(instance_id: str) -> Path:
    """Return the filesystem path for a named pipe owned by *instance_id*."""
    return _PIPE_DIR / f"{instance_id}.pipe"


class PipeMessage:
    """A single IPC message exchanged over a named pipe."""

    __slots__ = ("sender_id", "msg_type", "payload", "timestamp")

    def __init__(
        self,
        sender_id: str,
        msg_type: str,
        payload: dict[str, Any],
        timestamp: float | None = None,
    ) -> None:
        if msg_type not in VALID_MSG_TYPES:
            raise ValueError(f"Invalid msg_type: {msg_type!r}")
        self.sender_id = sender_id
        self.msg_type = msg_type
        self.payload = payload
        self.timestamp = timestamp or time.time()

    def to_json(self) -> str:
        return json.dumps({
            "sender_id": self.sender_id,
            "msg_type": self.msg_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, raw: str) -> PipeMessage:
        data = json.loads(raw)
        return cls(
            sender_id=data["sender_id"],
            msg_type=data["msg_type"],
            payload=data.get("payload", {}),
            timestamp=data.get("timestamp"),
        )


class PipeIPC:
    """Named-pipe IPC for same-machine multi-instance orchestration.

    Each instance creates a FIFO (Unix) or named pipe (Windows) that other
    instances can connect to by path.  Messages are newline-delimited JSON.
    """

    def __init__(self, instance_id: str | None = None, pipe_path: str | None = None) -> None:
        self.instance_id = instance_id or f"ccb-{uuid.uuid4().hex[:8]}"
        self.pipe_path = Path(pipe_path) if pipe_path else _pipe_path_for(self.instance_id)
        self._peers: dict[str, Path] = {}  # instance_id -> pipe path
        self._running = False
        self._reader_task: asyncio.Task[None] | None = None
        self._inbox: asyncio.Queue[PipeMessage] = asyncio.Queue()

    # ── Lifecycle ──

    def create_pipe(self) -> None:
        """Create the local named pipe (blocking)."""
        self.pipe_path.parent.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_pipes()

        if _IS_WINDOWS:
            # Windows named pipes require pywin32; fall back to a plain file
            # marker so that at least same-machine file-based discovery works.
            # Real Windows named-pipe I/O is out-of-scope for the MVP.
            if not self.pipe_path.exists():
                self.pipe_path.write_text("")
        else:
            # Unix FIFO
            if self.pipe_path.exists():
                self.pipe_path.unlink()
            os.mkfifo(str(self.pipe_path))

    async def connect_pipe(self, target_id: str, target_path: str | None = None) -> None:
        """Register a remote peer so we can send messages to it."""
        path = Path(target_path) if target_path else _pipe_path_for(target_id)
        if path.exists():
            self._peers[target_id] = path

    async def start(self) -> None:
        """Start the background reader loop."""
        if _IS_WINDOWS:
            return  # No FIFO reader on Windows (MVP)
        self._running = True
        self._reader_task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        """Stop the background reader and clean up."""
        self._running = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        self._remove_pipe()

    # ── Messaging ──

    async def send_message(
        self,
        target_id: str,
        msg_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Send a message to a specific instance by ID."""
        target_path = self._peers.get(target_id)
        if target_path is None:
            target_path = _pipe_path_for(target_id)
        if not target_path.exists():
            raise FileNotFoundError(f"Peer pipe not found: {target_path}")

        msg = PipeMessage(
            sender_id=self.instance_id,
            msg_type=msg_type,
            payload=payload or {},
        )
        await self._write_to_pipe(target_path, msg)

    async def receive_message(self, timeout: float = 5.0) -> PipeMessage | None:
        """Read the next message from the inbox.  Returns None on timeout."""
        try:
            return await asyncio.wait_for(self._inbox.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def broadcast(self, msg_type: str, payload: dict[str, Any] | None = None) -> int:
        """Broadcast a message to all known peers.  Returns number of peers reached."""
        sent = 0
        for peer_id in list(self._peers):
            try:
                await self.send_message(peer_id, msg_type, payload)
                sent += 1
            except (FileNotFoundError, OSError):
                # Peer pipe gone — drop it
                self._peers.pop(peer_id, None)
        return sent

    # ── Peer management ──

    def discover_local_peers(self) -> list[str]:
        """Scan the pipe directory for other instances."""
        self._cleanup_stale_pipes()
        peers: list[str] = []
        if not _PIPE_DIR.exists():
            return peers
        for entry in _PIPE_DIR.iterdir():
            if entry.suffix == ".pipe":
                peer_id = entry.stem
                if peer_id != self.instance_id:
                    peers.append(peer_id)
                    self._peers[peer_id] = entry
        return peers

    def list_peers(self) -> list[dict[str, Any]]:
        """Return info about known peers."""
        result: list[dict[str, Any]] = []
        for pid, path in self._peers.items():
            result.append({
                "id": pid,
                "path": str(path),
                "exists": path.exists(),
            })
        return result

    # ── Internal helpers ──

    async def _read_loop(self) -> None:
        """Continuously read from our FIFO and enqueue messages."""
        loop = asyncio.get_event_loop()
        buf = ""
        while self._running:
            try:
                data = await loop.run_in_executor(None, self._blocking_read)
                if data is None:
                    await asyncio.sleep(0.1)
                    continue
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = PipeMessage.from_json(line)
                        await self._inbox.put(msg)
                    except (json.JSONDecodeError, KeyError, ValueError):
                        pass
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.2)

    def _blocking_read(self) -> str | None:
        """Non-blocking read from FIFO (called in executor)."""
        try:
            fd = os.open(str(self.pipe_path), os.O_RDONLY | os.O_NONBLOCK)
            try:
                data = os.read(fd, 65536)
                return data.decode("utf-8", errors="replace") if data else None
            finally:
                os.close(fd)
        except (OSError, BlockingIOError):
            return None

    async def _write_to_pipe(self, path: Path, msg: PipeMessage) -> None:
        """Write a message to a peer's FIFO."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._blocking_write, path, msg)

    @staticmethod
    def _blocking_write(path: Path, msg: PipeMessage) -> None:
        data = msg.to_json().encode("utf-8")
        fd = os.open(str(path), os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def _remove_pipe(self) -> None:
        """Remove our FIFO from disk."""
        try:
            self.pipe_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _cleanup_stale_pipes(self) -> None:
        """Remove pipe files older than 5 minutes (likely abandoned)."""
        if not _PIPE_DIR.exists():
            return
        cutoff = time.time() - 300
        for entry in _PIPE_DIR.iterdir():
            if entry.suffix == ".pipe":
                try:
                    if entry.stat().st_mtime < cutoff:
                        entry.unlink(missing_ok=True)
                except OSError:
                    pass
