from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ccb.config import ccb_dir

_MAX_EVENT_BYTES = 1024 * 1024
_MAX_EVENT_LINES = 1000


def events_path() -> Path:
    return ccb_dir() / "events.jsonl"


def emit_event(
    kind: str,
    source: str,
    action: str = "",
    payload: dict[str, Any] | None = None,
    level: str = "info",
    cwd: str = "",
) -> dict[str, Any]:
    record = {
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "kind": kind,
        "source": source,
        "action": action,
        "cwd": cwd,
        "payload": payload or {},
    }
    path = events_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        if path.stat().st_size > _MAX_EVENT_BYTES:
            _trim_events(path)
    except OSError:
        pass
    return record


def recent_events(
    limit: int = 20,
    *,
    level: str | None = None,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    path = events_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(events) >= limit:
            break
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if level and event.get("level") != level:
            continue
        if kind and event.get("kind") != kind:
            continue
        events.append(event)
    return list(reversed(events))


def event_summary(limit: int = 200) -> dict[str, Any]:
    events = recent_events(limit)
    by_level: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    last_error: dict[str, Any] | None = None
    for event in events:
        level = str(event.get("level") or "info")
        kind = str(event.get("kind") or "unknown")
        by_level[level] = by_level.get(level, 0) + 1
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if level in ("error", "warning"):
            last_error = event
    return {
        "total": len(events),
        "by_level": by_level,
        "by_kind": by_kind,
        "last_problem": last_error,
    }


def clear_events() -> None:
    try:
        events_path().unlink(missing_ok=True)
    except OSError:
        pass


def _trim_events(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-_MAX_EVENT_LINES:]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    except OSError:
        pass
