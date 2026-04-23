"""Background daemon process for ccb-py.

Handles auto-update checks, marketplace refresh, and
background task scheduling.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any


_PID_FILE = Path.home() / ".claude" / "ccb-daemon.pid"
_LOG_FILE = Path.home() / ".claude" / "ccb-daemon.log"


class Daemon:
    """Background daemon for periodic tasks."""

    def __init__(self) -> None:
        self._running = False
        self._tasks: list[dict[str, Any]] = []
        self._interval = 3600  # 1 hour default check interval

    def add_periodic_task(self, name: str, fn: Any, interval: int = 3600) -> None:
        self._tasks.append({"name": name, "fn": fn, "interval": interval, "last_run": 0.0})

    async def run(self) -> None:
        """Main daemon loop."""
        self._running = True
        _write_pid()
        _log(f"Daemon started (pid={os.getpid()})")

        # Register signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_event_loop().add_signal_handler(sig, self.stop)

        while self._running:
            now = time.time()
            for task in self._tasks:
                if now - task["last_run"] >= task["interval"]:
                    try:
                        _log(f"Running task: {task['name']}")
                        result = task["fn"]()
                        if asyncio.iscoroutine(result):
                            await result
                        task["last_run"] = now
                    except Exception as e:
                        _log(f"Task {task['name']} failed: {e}")
            await asyncio.sleep(60)  # Check every minute

        _log("Daemon stopped")
        _remove_pid()

    def stop(self) -> None:
        self._running = False


def _write_pid() -> None:
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))


def _remove_pid() -> None:
    _PID_FILE.unlink(missing_ok=True)


def _log(msg: str) -> None:
    try:
        with _LOG_FILE.open("a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def is_running() -> int | None:
    """Check if daemon is running. Returns PID or None."""
    if not _PID_FILE.exists():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
        os.kill(pid, 0)  # Check if process exists
        return pid
    except (ValueError, OSError):
        _PID_FILE.unlink(missing_ok=True)
        return None


def start_daemon() -> int | None:
    """Start the daemon in background. Returns PID."""
    existing = is_running()
    if existing:
        return existing
    # Fork to background
    pid = os.fork()
    if pid > 0:
        return pid  # Parent returns child PID
    # Child: create new session
    os.setsid()
    # Second fork
    pid2 = os.fork()
    if pid2 > 0:
        sys.exit(0)

    # Daemon process
    sys.stdin.close()
    daemon = Daemon()

    # Register default periodic tasks
    def check_updates() -> None:
        _log("Checking for updates...")

    def refresh_marketplaces() -> None:
        try:
            from ccb.plugins import marketplace_list, marketplace_update
            for m in marketplace_list():
                marketplace_update(m["name"])
                _log(f"Refreshed marketplace: {m['name']}")
        except Exception as e:
            _log(f"Marketplace refresh failed: {e}")

    daemon.add_periodic_task("check_updates", check_updates, interval=86400)
    daemon.add_periodic_task("refresh_marketplaces", refresh_marketplaces, interval=3600)

    asyncio.run(daemon.run())
    sys.exit(0)


def stop_daemon() -> bool:
    """Stop the daemon if running."""
    pid = is_running()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        _remove_pid()
        return True
    except OSError:
        _remove_pid()
        return False


def daemon_status() -> dict[str, Any]:
    """Get daemon status."""
    pid = is_running()
    return {
        "running": pid is not None,
        "pid": pid,
        "pid_file": str(_PID_FILE),
        "log_file": str(_LOG_FILE),
    }
