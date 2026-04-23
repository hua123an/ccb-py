"""ContextCollapse — collapse old context to save token space.

Replaces middle messages with one-line summaries when context usage
exceeds a threshold. Keeps head (system prompt + first user msg) and
tail (recent messages) intact.

Collapse lifecycle:
  1. projectView() applies existing commits to produce the visible message list
  2. applyCollapsesIfNeeded() checks token usage vs threshold, collapses if needed
  3. recoverFromOverflow() emergency-collapses when prompt is too long
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

# ── Constants matching original TS ─────────────────────────────

KEEP_HEAD_MESSAGES = 2
KEEP_TAIL_MESSAGES = 8
EMERGENCY_KEEP_TAIL_MESSAGES = 4
MIN_COLLAPSIBLE_MESSAGES = 6
COLLAPSE_TRIGGER_RATIO = 0.72
SUMMARY_LINE_LIMIT = 12


# ── Types ──────────────────────────────────────────────────────

@dataclass
class CommitRecord:
    collapse_id: str
    summary_uuid: str
    summary_content: str
    summary: str
    first_archived_uuid: str
    last_archived_uuid: str
    archived_count: int = 0


@dataclass
class CollapseHealth:
    total_spawns: int = 0
    total_errors: int = 0
    last_error: str | None = None
    empty_spawn_warning_emitted: bool = False
    total_empty_spawns: int = 0


@dataclass
class CollapseStats:
    collapsed_spans: int = 0
    collapsed_messages: int = 0
    staged_spans: int = 0
    health: CollapseHealth = field(default_factory=CollapseHealth)


# ── Store (module-level singleton) ─────────────────────────────

_commits: list[CommitRecord] = []
_next_id: int = 1
_health = CollapseHealth()
_listeners: list[Any] = []


def _notify() -> None:
    for cb in _listeners:
        try:
            cb()
        except Exception:
            pass


def _next_collapse_id() -> str:
    global _next_id
    cid = str(_next_id).zfill(16)
    _next_id += 1
    return cid


def _truncate(text: str, max_len: int = 180) -> str:
    compact = " ".join(text.split()).strip()
    if not compact:
        return ""
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3].rstrip() + "..."


# ── Message summarization ──────────────────────────────────────

def _summarize_message(msg: Any) -> str:
    """Summarize a single message to one line."""
    from ccb.api.base import Role

    role = getattr(msg, "role", None)
    content = getattr(msg, "content", "") or ""

    if role == Role.ASSISTANT:
        if content:
            return f"Assistant: {_truncate(content)}"
        tool_calls = getattr(msg, "tool_calls", []) or []
        if tool_calls:
            names = ", ".join(tc.name for tc in tool_calls if hasattr(tc, "name"))
            return f"Assistant used tools: {names}"
        return "Assistant responded with structured content"

    if role == Role.USER:
        if content:
            return f"User: {_truncate(content)}"
        tool_results = getattr(msg, "tool_results", []) or []
        if tool_results:
            first = tool_results[0]
            tr_content = getattr(first, "content", "")
            return f"Tool result: {_truncate(str(tr_content), 140)}"
        return "User sent structured content"

    # System or other
    return f"System: {_truncate(str(content))}" if content else ""


def _build_collapsed_summary(archived: list[Any]) -> str:
    """Build a multi-line collapsed summary."""
    lines = [_summarize_message(m) for m in archived]
    lines = [l for l in lines if l][:SUMMARY_LINE_LIMIT]
    extra = max(len(archived) - len(lines), 0)
    if extra > 0:
        lines.append(f"... {extra} more earlier messages")
    if not lines:
        lines.append(f"Collapsed {len(archived)} earlier messages")
    return "\n".join(lines)


# ── Core collapse logic ────────────────────────────────────────

def _get_collapse_candidate(
    messages: list[Any],
    keep_tail: int,
) -> tuple[list[Any], str, str] | None:
    """Find a collapsible span. Returns (archived, first_uuid, last_uuid) or None."""
    total = len(messages)
    if total <= KEEP_HEAD_MESSAGES + keep_tail + MIN_COLLAPSIBLE_MESSAGES:
        return None

    start = KEEP_HEAD_MESSAGES
    end = total - keep_tail - 1
    if end < start:
        return None

    archived = messages[start : end + 1]
    if len(archived) < MIN_COLLAPSIBLE_MESSAGES:
        return None

    first_uuid = getattr(archived[0], "id", "") or str(uuid.uuid4())
    last_uuid = getattr(archived[-1], "id", "") or str(uuid.uuid4())
    return archived, first_uuid, last_uuid


def _create_summary_message(summary_uuid: str, content: str) -> Any:
    """Create a synthetic assistant message with the collapsed summary."""
    from ccb.api.base import Message, Role

    return Message(
        role=Role.ASSISTANT,
        content=content,
        id=summary_uuid,
    )


def _project_commit(
    messages: list[Any],
    commit: CommitRecord,
) -> tuple[list[Any], int]:
    """Apply a single commit to the message list."""
    start_idx = -1
    end_idx = -1
    for i, m in enumerate(messages):
        mid = getattr(m, "id", "")
        if mid == commit.first_archived_uuid:
            start_idx = i
        if mid == commit.last_archived_uuid:
            end_idx = i

    if start_idx == -1 or end_idx == -1 or end_idx < start_idx:
        return messages, 0

    collapsed_count = end_idx - start_idx + 1
    replacement = _create_summary_message(
        commit.summary_uuid, commit.summary_content
    )
    return (
        messages[:start_idx] + [replacement] + messages[end_idx + 1 :],
        collapsed_count,
    )


# ── Public API ─────────────────────────────────────────────────

def init_context_collapse() -> None:
    """Reset the collapse store. Call once at startup."""
    global _commits, _next_id, _health
    _commits = []
    _next_id = 1
    _health = CollapseHealth()
    _notify()


def get_stats() -> CollapseStats:
    collapsed_msgs = sum(c.archived_count for c in _commits)
    return CollapseStats(
        collapsed_spans=len(_commits),
        collapsed_messages=collapsed_msgs,
        staged_spans=0,
        health=CollapseHealth(
            total_spawns=_health.total_spawns,
            total_errors=_health.total_errors,
            last_error=_health.last_error,
            empty_spawn_warning_emitted=_health.empty_spawn_warning_emitted,
            total_empty_spawns=_health.total_empty_spawns,
        ),
    )


def subscribe(callback: Any) -> Any:
    """Subscribe to collapse state changes. Returns unsubscribe fn."""
    _listeners.append(callback)
    return lambda: _listeners.remove(callback) if callback in _listeners else None


def project_view(messages: list[Any]) -> list[Any]:
    """Apply all committed collapses to the message list."""
    projected = list(messages)
    total_collapsed = 0
    for commit in _commits:
        projected, count = _project_commit(projected, commit)
        if count > 0 and commit.archived_count == 0:
            commit.archived_count = count
        total_collapsed += count
    if total_collapsed > 0:
        _notify()
    return projected


def apply_collapses_if_needed(
    messages: list[Any],
    model: str = "",
    context_limit: int = 200_000,
) -> list[Any]:
    """Check token usage and collapse if needed.

    Args:
        messages: current conversation messages
        model: model name (for context window lookup)
        context_limit: max context tokens

    Returns:
        Possibly collapsed message list.
    """
    projected = project_view(messages)

    _health.total_spawns += 1

    # Estimate token count
    total_chars = sum(len(getattr(m, "content", "") or "") for m in projected)
    est_tokens = total_chars // 4

    threshold = int(context_limit * COLLAPSE_TRIGGER_RATIO)
    if est_tokens < threshold:
        _health.total_empty_spawns += 1
        if _health.total_empty_spawns >= 3:
            _health.empty_spawn_warning_emitted = True
        _notify()
        return projected

    # Perform collapse
    try:
        candidate = _get_collapse_candidate(projected, KEEP_TAIL_MESSAGES)
        if not candidate:
            return projected

        archived, first_uuid, last_uuid = candidate
        cid = _next_collapse_id()
        summary = _build_collapsed_summary(archived)
        summary_uuid = str(uuid.uuid4())

        commit = CommitRecord(
            collapse_id=cid,
            summary_uuid=summary_uuid,
            summary_content=f'<collapsed id="{cid}">{summary}</collapsed>',
            summary=summary,
            first_archived_uuid=first_uuid,
            last_archived_uuid=last_uuid,
            archived_count=len(archived),
        )
        _commits.append(commit)
        _health.total_empty_spawns = 0
        _health.empty_spawn_warning_emitted = False
        _notify()

        return project_view(messages)

    except Exception as e:
        _health.total_errors += 1
        _health.last_error = str(e)
        _notify()
        return projected


def recover_from_overflow(messages: list[Any]) -> tuple[int, list[Any]]:
    """Emergency collapse when prompt exceeds model limit.

    Returns (committed_count, collapsed_messages).
    """
    projected = project_view(messages)
    candidate = _get_collapse_candidate(projected, EMERGENCY_KEEP_TAIL_MESSAGES)
    if not candidate:
        return 0, projected

    archived, first_uuid, last_uuid = candidate
    cid = _next_collapse_id()
    summary = _build_collapsed_summary(archived)
    summary_uuid = str(uuid.uuid4())

    commit = CommitRecord(
        collapse_id=cid,
        summary_uuid=summary_uuid,
        summary_content=f'<collapsed id="{cid}">{summary}</collapsed>',
        summary=summary,
        first_archived_uuid=first_uuid,
        last_archived_uuid=last_uuid,
        archived_count=len(archived),
    )
    _commits.append(commit)
    _health.total_empty_spawns = 0
    _health.empty_spawn_warning_emitted = False
    _notify()

    return 1, project_view(projected)


def restore_from_entries(entries: list[dict[str, Any]]) -> None:
    """Restore collapse state from persisted entries."""
    global _next_id
    for entry in entries:
        cid = entry.get("collapse_id", "")
        try:
            num = int(cid)
            if num >= _next_id:
                _next_id = num + 1
        except (ValueError, TypeError):
            pass

        _commits.append(CommitRecord(
            collapse_id=cid,
            summary_uuid=entry.get("summary_uuid", ""),
            summary_content=entry.get("summary_content", ""),
            summary=entry.get("summary", ""),
            first_archived_uuid=entry.get("first_archived_uuid", ""),
            last_archived_uuid=entry.get("last_archived_uuid", ""),
            archived_count=0,
        ))
    _notify()
