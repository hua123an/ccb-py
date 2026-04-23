"""SessionTranscript — export session as markdown transcript.

Generates a clean, human-readable markdown document from a session's
messages, suitable for sharing, archiving, or pasting into docs.
"""
from __future__ import annotations

import time
from typing import Any


def export_transcript(session: Any, include_tools: bool = True) -> str:
    """Export a session as a markdown transcript.

    Args:
        session: Session object with .messages, .model, .id, .cwd
        include_tools: Whether to include tool call/result details

    Returns:
        Markdown string of the transcript.
    """
    from ccb.api.base import Role

    lines: list[str] = []

    # Header
    lines.append(f"# Session Transcript")
    lines.append("")
    lines.append(f"- **Session ID**: {session.id}")
    lines.append(f"- **Model**: {session.model}")
    lines.append(f"- **CWD**: {session.cwd}")
    lines.append(f"- **Messages**: {len(session.messages)}")
    lines.append(f"- **Exported**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, msg in enumerate(session.messages):
        role = getattr(msg, "role", None)
        content = getattr(msg, "content", "") or ""

        if role == Role.USER:
            lines.append("## 🧑 You")
            lines.append("")
            if content:
                lines.append(content)
                lines.append("")

            # Tool results
            if include_tools:
                tool_results = getattr(msg, "tool_results", []) or []
                for tr in tool_results:
                    tr_content = getattr(tr, "content", "")
                    is_error = getattr(tr, "is_error", False)
                    prefix = "❌ Error" if is_error else "✅ Result"
                    lines.append(f"> {prefix}: {_truncate(str(tr_content), 200)}")
                    lines.append("")

            # Images
            images = getattr(msg, "images", []) or []
            if images:
                names = ", ".join(
                    img.get("filename", "image") for img in images
                )
                lines.append(f"📎 *Images: {names}*")
                lines.append("")

        elif role == Role.ASSISTANT:
            lines.append("## 🤖 Claude")
            lines.append("")
            if content:
                lines.append(content)
                lines.append("")

            # Tool calls
            if include_tools:
                tool_calls = getattr(msg, "tool_calls", []) or []
                for tc in tool_calls:
                    name = getattr(tc, "name", "?")
                    inp = getattr(tc, "input", {}) or {}
                    summary = _summarize_tool(name, inp)
                    lines.append(f"> ⏺ **{name}** {summary}")
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def export_transcript_compact(session: Any) -> str:
    """Export a compact transcript (no tool details, shorter)."""
    return export_transcript(session, include_tools=False)


def _truncate(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _summarize_tool(name: str, inp: dict[str, Any]) -> str:
    """Short summary of a tool call input."""
    if name == "bash":
        cmd = inp.get("command", "")[:80]
        return f"`{cmd}`"
    if name in ("file_read", "file_write", "file_edit"):
        return inp.get("file_path", "")
    if name == "grep":
        return f"'{inp.get('pattern', '')}' in {inp.get('path', '.')}"
    if name == "glob":
        return f"'{inp.get('pattern', '')}'"
    if name == "agent":
        return inp.get("task", "")[:80]
    return ""
