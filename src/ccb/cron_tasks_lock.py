"""Best-effort per-project scheduled-task lock."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ccb.json_store import read_json

LOCK_REL = Path(".claude") / "scheduled_tasks.lock"
STALE_AFTER_MS = 30_000


def _lock_path(project_dir: str | Path | None = None) -> Path:
    return Path(project_dir or Path.cwd()) / LOCK_REL


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def try_acquire_scheduler_lock(project_dir: str | Path | None = None,
                               owner: str | None = None) -> bool:
    path = _lock_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "owner": owner or str(os.getpid()), "time": int(time.time() * 1000)}
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags)
    except FileExistsError:
        try:
            data = read_json(path, default={})
            if not isinstance(data, dict):
                data = {}
            pid = int(data.get("pid", 0))
            ts = int(data.get("time", 0))
        except Exception:
            pid, ts = 0, 0
        stale = int(time.time() * 1000) - ts > STALE_AFTER_MS
        if pid == os.getpid() or stale or not _process_alive(pid):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                return False
            return try_acquire_scheduler_lock(project_dir, owner)
        return False
    else:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        return True


def release_scheduler_lock(project_dir: str | Path | None = None) -> None:
    path = _lock_path(project_dir)
    try:
        data = read_json(path, default={})
        if not isinstance(data, dict):
            data = {}
        if int(data.get("pid", 0)) not in (0, os.getpid()):
            return
    except Exception:
        pass
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
