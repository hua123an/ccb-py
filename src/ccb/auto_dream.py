"""AutoDream — background memory consolidation.

Fires a /dream prompt as a forked subagent when time-gate passes AND
enough sessions have accumulated since last consolidation.

Gate order (cheapest first):
  1. Time: hours since lastConsolidatedAt >= minHours
  2. Sessions: session count with mtime > lastConsolidatedAt >= minSessions
  3. Lock: no other process mid-consolidation
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────

@dataclass
class AutoDreamConfig:
    min_hours: float = 24.0
    min_sessions: int = 5
    enabled: bool = True


def _config_dir() -> Path:
    return Path.home() / ".claude"


def _lock_path() -> Path:
    return _config_dir() / "auto-dream-lock.json"


def _sessions_dir() -> Path:
    return _config_dir() / "sessions"


# ── Lock management ───────────────────────────────────────────

def read_last_consolidated_at() -> float:
    """Return epoch-seconds of last consolidation (0 if never)."""
    lp = _lock_path()
    if lp.exists():
        try:
            data = json.loads(lp.read_text())
            return float(data.get("last_consolidated_at", 0))
        except Exception:
            pass
    return 0.0


def _write_lock(ts: float) -> None:
    lp = _lock_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps({
        "last_consolidated_at": ts,
        "pid": os.getpid(),
    }))


def try_acquire_lock() -> float | None:
    """Attempt to acquire the consolidation lock.

    Returns the prior mtime (for rollback) on success, None if locked.
    """
    lp = _lock_path()
    prior = read_last_consolidated_at()
    if lp.exists():
        try:
            data = json.loads(lp.read_text())
            pid = data.get("pid", 0)
            if pid and pid != os.getpid():
                # Check if locking process is still alive
                try:
                    os.kill(pid, 0)
                    return None  # another process holds the lock
                except ProcessLookupError:
                    pass  # dead process, safe to take over
        except Exception:
            pass
    _write_lock(time.time())
    return prior


def rollback_lock(prior_mtime: float) -> None:
    """Rewind the lock timestamp so the time-gate passes again."""
    _write_lock(prior_mtime)


def list_sessions_touched_since(since: float) -> list[str]:
    """List session IDs with mtime > since."""
    sd = _sessions_dir()
    if not sd.is_dir():
        return []
    result = []
    for f in sd.iterdir():
        if f.suffix == ".json" and f.stat().st_mtime > since:
            result.append(f.stem)
    return result


# ── Dream task tracking ───────────────────────────────────────

@dataclass
class DreamTask:
    task_id: str
    sessions_reviewing: int = 0
    status: str = "running"  # running | completed | failed | killed
    turns: list[dict[str, Any]] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    prior_mtime: float = 0.0

    def add_turn(self, text: str, tool_count: int, paths: list[str]) -> None:
        self.turns.append({"text": text, "tool_count": tool_count})
        for p in paths:
            if p not in self.files_touched:
                self.files_touched.append(p)


_dream_tasks: dict[str, DreamTask] = {}


# ── Consolidation prompt ──────────────────────────────────────

def build_consolidation_prompt(
    memory_root: str,
    transcript_dir: str,
    session_ids: list[str],
) -> str:
    """Build the dream consolidation prompt."""
    sessions_list = "\n".join(f"- {sid}" for sid in session_ids)
    return f"""You are a memory consolidation agent. Review the recent session transcripts
and update the project memory files under {memory_root}/.

Your goal:
1. Read recent session transcripts from {transcript_dir}/
2. Extract key learnings, decisions, patterns, and important context
3. Update or create memory files in {memory_root}/
4. Organize memories by topic (architecture, bugs, decisions, preferences)
5. Remove stale or superseded memories
6. Keep each memory file focused and concise

Sessions to review ({len(session_ids)}):
{sessions_list}

Rules:
- Bash is restricted to read-only commands (ls, find, grep, cat, stat, wc, head, tail)
- Use file_write and file_edit for memory files only
- Do NOT modify any project source code
- Summarize patterns across sessions, not individual messages
- Prefer updating existing memory files over creating new ones"""


# ── Main engine ───────────────────────────────────────────────

SESSION_SCAN_INTERVAL = 600  # 10 minutes

_last_session_scan: float = 0.0
_initialized: bool = False


def init_auto_dream() -> None:
    """Initialize the auto-dream system. Call once at startup."""
    global _last_session_scan, _initialized
    _last_session_scan = 0.0
    _dream_tasks.clear()
    _initialized = True


async def execute_auto_dream(
    session: Any,
    provider: Any | None = None,
    registry: Any | None = None,
    on_progress: Callable[[str], None] | None = None,
    config: AutoDreamConfig | None = None,
) -> DreamTask | None:
    """Entry point — called from post-sampling hook.

    Returns the DreamTask if a dream was executed, None otherwise.
    """
    global _last_session_scan
    if not _initialized:
        return None

    cfg = config or AutoDreamConfig()
    if not cfg.enabled:
        return None

    # --- Time gate ---
    last_at = read_last_consolidated_at()
    hours_since = (time.time() - last_at) / 3600
    if hours_since < cfg.min_hours:
        return None

    # --- Scan throttle ---
    since_scan = time.time() - _last_session_scan
    if since_scan < SESSION_SCAN_INTERVAL:
        logger.debug(
            "autoDream scan throttle — last scan was %.0fs ago", since_scan
        )
        return None
    _last_session_scan = time.time()

    # --- Session gate ---
    session_ids = list_sessions_touched_since(last_at)
    # Exclude current session
    current_id = getattr(session, "id", "")
    session_ids = [s for s in session_ids if s != current_id]
    if len(session_ids) < cfg.min_sessions:
        logger.debug(
            "autoDream skip — %d sessions, need %d",
            len(session_ids), cfg.min_sessions,
        )
        return None

    # --- Lock ---
    prior_mtime = try_acquire_lock()
    if prior_mtime is None:
        return None

    logger.info(
        "autoDream firing — %.1fh since last, %d sessions to review",
        hours_since, len(session_ids),
    )

    # Create dream task
    import uuid
    task_id = str(uuid.uuid4())[:8]
    task = DreamTask(
        task_id=task_id,
        sessions_reviewing=len(session_ids),
        prior_mtime=prior_mtime,
    )
    _dream_tasks[task_id] = task

    try:
        memory_root = str(_config_dir() / "memory")
        transcript_dir = str(_sessions_dir())
        prompt = build_consolidation_prompt(
            memory_root, transcript_dir, session_ids
        )

        if provider and registry:
            # Use forked agent for real dream execution
            from ccb.api.base import Message as Msg, Role
            from ccb.loop import run_turn

            dream_session_cls = type(session)
            dream_session = dream_session_cls(
                model=session.model,
                cwd=session.cwd,
            )
            dream_session.add_user_message(prompt)

            if on_progress:
                on_progress(f"Dreaming... reviewing {len(session_ids)} sessions")

            await run_turn(
                provider, dream_session, registry,
                "You are a memory consolidation agent. Be concise.",
            )

            # Collect files touched from tool calls
            for msg in dream_session.messages:
                if msg.role == Role.ASSISTANT:
                    for tc in (msg.tool_calls or []):
                        if tc.name in ("file_write", "file_edit"):
                            fp = (tc.input or {}).get("file_path", "")
                            if fp:
                                task.files_touched.append(fp)
                        task.add_turn(
                            msg.content or "", len(msg.tool_calls or []),
                            [],
                        )

        task.status = "completed"
        if on_progress:
            on_progress(
                f"Dream complete — touched {len(task.files_touched)} files"
            )
        logger.info(
            "autoDream completed — %d files touched",
            len(task.files_touched),
        )

    except asyncio.CancelledError:
        task.status = "killed"
        rollback_lock(prior_mtime)
        logger.info("autoDream aborted by user")
    except Exception as e:
        task.status = "failed"
        rollback_lock(prior_mtime)
        logger.error("autoDream failed: %s", e)

    return task


def get_dream_tasks() -> dict[str, DreamTask]:
    return dict(_dream_tasks)


def kill_dream_task(task_id: str) -> bool:
    """Kill a running dream task."""
    task = _dream_tasks.get(task_id)
    if task and task.status == "running":
        task.status = "killed"
        rollback_lock(task.prior_mtime)
        return True
    return False
