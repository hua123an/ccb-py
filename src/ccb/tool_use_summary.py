"""ToolUseSummary — generate human-readable summaries of completed tool batches.

Uses a lightweight model (Haiku) to produce short labels describing what
a batch of tool calls accomplished. Used by SDK/API mode to provide
high-level progress updates to clients.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Write a short summary label describing what these tool calls accomplished. \
It appears as a single-line row in a mobile app and truncates around 30 characters, \
so think git-commit-subject, not sentence."""


async def generate_tool_use_summary(
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    provider: Any | None = None,
) -> str:
    """Generate a short summary of a tool batch.

    Args:
        tool_calls: list of {name, input} dicts
        tool_results: list of {content, is_error} dicts
        provider: API provider for LLM call (optional, falls back to heuristic)

    Returns:
        Short summary string (max ~40 chars).
    """
    # Build a description of the tool batch
    descriptions = []
    for tc, tr in zip(tool_calls, tool_results):
        name = tc.get("name", "?")
        inp = tc.get("input", {})
        output = tr.get("content", "")
        is_error = tr.get("is_error", False)
        desc = _describe_tool(name, inp, output, is_error)
        descriptions.append(desc)

    batch_desc = "\n".join(descriptions)

    # Try LLM summarization
    if provider:
        try:
            from ccb.api.base import Message, Role
            prompt = f"Tool calls:\n{batch_desc}\n\nSummary label (max 30 chars):"
            resp = await provider.complete(
                messages=[Message(role=Role.USER, content=prompt)],
                system_prompt=SYSTEM_PROMPT,
                model=None,
            )
            if resp and resp.content:
                return resp.content.strip()[:40]
        except Exception as e:
            logger.debug("Tool use summary LLM failed: %s", e)

    # Fallback: heuristic summary
    return _heuristic_summary(tool_calls, tool_results)


def _heuristic_summary(
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> str:
    """Generate a heuristic summary without LLM."""
    if not tool_calls:
        return "No tools used"

    names = [tc.get("name", "?") for tc in tool_calls]
    unique = list(dict.fromkeys(names))  # preserve order, dedupe

    if len(unique) == 1:
        name = unique[0]
        count = len(names)
        if name == "bash":
            cmds = [tc.get("input", {}).get("command", "")[:30] for tc in tool_calls]
            return f"Ran {count} command{'s' if count > 1 else ''}"
        if name == "file_read":
            return f"Read {count} file{'s' if count > 1 else ''}"
        if name == "file_write":
            return f"Wrote {count} file{'s' if count > 1 else ''}"
        if name == "file_edit":
            return f"Edited {count} file{'s' if count > 1 else ''}"
        if name == "grep":
            return f"Searched {count} pattern{'s' if count > 1 else ''}"
        if name == "glob":
            return f"Listed files"
        return f"Used {name}" + (f" ×{count}" if count > 1 else "")

    if len(unique) <= 3:
        return f"Used {', '.join(unique)}"

    return f"Used {len(tool_calls)} tools ({len(unique)} types)"


def _describe_tool(
    name: str,
    inp: dict[str, Any],
    output: str,
    is_error: bool,
) -> str:
    """Describe a single tool call for the summary prompt."""
    status = "ERROR" if is_error else "OK"
    if name == "bash":
        cmd = inp.get("command", "")[:60]
        return f"bash: `{cmd}` → {status}"
    if name == "file_read":
        path = inp.get("file_path", "")
        lines = output.count("\n") + 1
        return f"file_read: {path} ({lines} lines) → {status}"
    if name == "file_write":
        path = inp.get("file_path", "")
        return f"file_write: {path} → {status}"
    if name == "file_edit":
        path = inp.get("file_path", "")
        return f"file_edit: {path} → {status}"
    if name == "grep":
        pattern = inp.get("pattern", "")
        return f"grep: '{pattern}' → {status}"
    return f"{name} → {status}"


def summarize_batch_sync(
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> str:
    """Synchronous heuristic-only summary (no LLM)."""
    return _heuristic_summary(tool_calls, tool_results)
