"""Background daemon process for ccb-py.

Handles auto-update checks, marketplace refresh, and
background task scheduling.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any


_PID_FILE = Path.home() / ".ccb" / "ccb-daemon.pid"
_LOG_FILE = Path.home() / ".ccb" / "ccb-daemon.log"


class Daemon:
    """Background daemon for periodic tasks."""

    def __init__(self) -> None:
        self._running = False
        self._tasks: list[dict[str, Any]] = []
        self._cleanup: list[Any] = []
        self._interval = 3600  # 1 hour default check interval

    def add_periodic_task(self, name: str, fn: Any, interval: int = 3600) -> None:
        self._tasks.append({"name": name, "fn": fn, "interval": interval, "last_run": 0.0})

    def add_cleanup(self, fn: Any) -> None:
        self._cleanup.append(fn)

    async def run(self) -> None:
        """Main daemon loop."""
        self._running = True
        _write_pid()
        _log(f"Daemon started (pid={os.getpid()})")
        _event("daemon", "started", {"pid": os.getpid(), "log_file": str(_LOG_FILE)})

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
                        _event("daemon_task", "failed", {"task": task["name"], "error": str(e)}, level="error")
            await asyncio.sleep(60)  # Check every minute

        for fn in self._cleanup:
            try:
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                _log(f"Cleanup failed: {e}")
        _log("Daemon stopped")
        _event("daemon", "stopped", {"pid": os.getpid()})
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


def _event(kind: str, action: str = "", payload: dict[str, Any] | None = None, level: str = "info") -> None:
    try:
        from ccb.events import emit_event
        emit_event(kind, "daemon", action, payload or {}, level=level)
    except Exception:
        pass


class ScheduledCronDaemon:
    def __init__(self) -> None:
        self._schedulers: dict[str, Any] = {}

    async def refresh(self) -> None:
        from ccb.cron_scheduler import CronScheduler
        from ccb.cron_tasks import list_scheduled_project_dirs
        from ccb.feature_flags import is_feature_enabled

        if not is_feature_enabled("scheduled_tasks", True):
            await self.stop()
            return
        projects = set(list_scheduled_project_dirs())
        for project in list(self._schedulers):
            if project not in projects:
                await self._schedulers.pop(project).stop()
        for project in sorted(projects):
            if project in self._schedulers:
                continue
            scheduler = CronScheduler(
                project_dir=project,
                on_fire=lambda prompt, p=project: self._enqueue_prompt(p, prompt),
                on_fire_task=lambda task, p=project: self._enqueue_task(p, task),
                on_missed=lambda tasks, p=project: self._enqueue_missed(p, tasks),
                assistant_mode=True,
                lock_owner=f"daemon:{os.getpid()}",
            )
            scheduler.start()
            self._schedulers[project] = scheduler
            _log(f"Started scheduled task watcher: {project}")
            _event("cron", "watcher_started", {"project": project})

    async def stop(self) -> None:
        for scheduler in list(self._schedulers.values()):
            await scheduler.stop()
        self._schedulers.clear()

    def _enqueue_prompt(self, project: str, prompt: str) -> None:
        from ccb.jobs import get_job_manager

        job = get_job_manager().create_job("cron", prompt, cwd=project, template_file=".claude/scheduled_tasks.json")
        _log(f"Queued scheduled task job {job.id} for {project}")
        _event("cron", "job_queued", {"job_id": job.id, "project": project})

    def _enqueue_task(self, project: str, task: Any) -> None:
        from ccb.jobs import get_job_manager

        job = get_job_manager().create_job(
            f"cron:{task.id}",
            task.prompt,
            cwd=project,
            template_file=".claude/scheduled_tasks.json",
        )
        _log(f"Queued scheduled task job {job.id} for task {task.id} in {project}")
        _event("cron", "job_queued", {"job_id": job.id, "task_id": task.id, "project": project})

    def _enqueue_missed(self, project: str, tasks: list[Any]) -> None:
        for task in tasks:
            self._enqueue_task(project, task)


async def _process_queued_jobs() -> None:
    from ccb.config import get_api_key
    from ccb.jobs import JobStatus, get_job_manager

    if not get_api_key():
        _log("Skipping queued jobs: no API key configured")
        _event("jobs", "skipped_no_api_key", {}, level="warning")
        return
    manager = get_job_manager()
    for job in manager.list_jobs(status=JobStatus.QUEUED)[:2]:
        try:
            from ccb.api.router import create_provider
            from ccb.config import get_model
            from ccb.tools.base import create_default_registry

            provider = create_provider(model=get_model())
            registry = create_default_registry(job.cwd)
            await manager.execute_job(job.id, provider, registry, on_progress=_log)
        except Exception as e:
            _log(f"Job {job.id} failed to start: {e}")
            _event("jobs", "failed_to_start", {"job_id": job.id, "error": str(e)}, level="error")


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
    from ccb.feature_flags import is_feature_enabled

    if not is_feature_enabled("tengu_daemon_enabled", True):
        _event("daemon", "start_blocked", {"reason": "feature_disabled"}, level="warning")
        return None
    existing = is_running()
    if existing:
        _event("daemon", "already_running", {"pid": existing})
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
    cron_daemon = ScheduledCronDaemon()

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
    daemon.add_periodic_task("scheduled_projects", cron_daemon.refresh, interval=60)
    daemon.add_periodic_task("process_jobs", _process_queued_jobs, interval=60)
    daemon.add_cleanup(cron_daemon.stop)

    asyncio.run(daemon.run())
    sys.exit(0)


def stop_daemon() -> bool:
    """Stop the daemon if running."""
    pid = is_running()
    if pid is None:
        _event("daemon", "stop_skipped", {"reason": "not_running"})
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        _remove_pid()
        _event("daemon", "stop_requested", {"pid": pid})
        return True
    except OSError:
        _remove_pid()
        _event("daemon", "stop_failed", {"pid": pid}, level="error")
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
