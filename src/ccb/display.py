"""Rich-based terminal display for streaming, tool output, and messages.

In full-screen REPL mode (repl.py), output is routed to the REPL's message
buffer instead of printing directly to stderr.  The helper ``_out()`` checks
for an active REPL and delegates accordingly.
"""
from __future__ import annotations

import shutil
from io import StringIO
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

# Official Claude Code color palette (dark terminal)
CLAUDE_ORANGE = "#d77757"       # rgb(215,119,87) — brand orange
LABEL_BLUE = "#2563eb"          # rgb(37,99,235)  — "You" label
LABEL_ORANGE = "#d77757"        # same as brand   — "Claude" label
BORDER_DIM = "#555555"           # │ prefix for tool results
BG_USER = "#1a1a2e"             # user message background tint

# Enhanced UI palette
STATUS_BAR_BG = "#1a1a2e"
STATUS_BAR_TEXT = "#e0e0e0"
HIGHLIGHT_CYAN = "#22d3ee"
HIGHLIGHT_GREEN = "#4ade80"
HIGHLIGHT_YELLOW = "#fbbf24"
HIGHLIGHT_MAGENTA = "#e879f9"


THEME = Theme({
    "user": f"bold {LABEL_BLUE}",
    "assistant": f"bold {CLAUDE_ORANGE}",
    "tool": "bold white",
    "tool.dot": f"{CLAUDE_ORANGE}",
    "tool.dim": "dim",
    "error": "bold red",
    "dim": "dim",
    "success": "green",
    "border": f"{BORDER_DIM}",
    "label.you": f"bold {LABEL_BLUE}",
    "label.claude": f"bold {LABEL_ORANGE}",
    "status.bar": f"bold {STATUS_BAR_TEXT}",
    "status.model": f"bold {HIGHLIGHT_CYAN}",
    "status.context": f"{HIGHLIGHT_GREEN}",
    "status.memory": f"{HIGHLIGHT_MAGENTA}",
    "spinner": f"{CLAUDE_ORANGE}",
    "progress.bar": f"{HIGHLIGHT_CYAN}",
    "progress.done": f"{HIGHLIGHT_GREEN}",
    "msg.timestamp": "#6b7280",
    "tool.running": f"{HIGHLIGHT_CYAN}",
    "tool.success": f"{HIGHLIGHT_GREEN}",
    "tool.error": "bold #ef4444",
})

console = Console(theme=THEME, stderr=True)


# ────────────────────────────────────────────────────────────────────
# Parallel agent progress registry
# ────────────────────────────────────────────────────────────────────
# When 2+ subagents run concurrently, per-tool output from inside each agent
# is suppressed (see print_tool_call / print_tool_result). Instead we keep a
# tiny per-agent stats dict here, and the parent REPL renders one compact
# progress line per active agent.

_agent_registry: dict[str, dict[str, Any]] = {}


def agent_register(label: str, task: str) -> None:
    """Called by run_agent when a subagent starts. Label is unique per agent."""
    import time
    _agent_registry[label] = {
        "task": task[:80],
        "started": time.time(),
        "tool_count": 0,
        "last_tool": "",
        "done": False,
        "duration": 0.0,
        "result_len": 0,
    }
    _render_agent_dashboard()


def agent_complete(label: str, result: str) -> None:
    import time
    if label in _agent_registry:
        e = _agent_registry[label]
        e["done"] = True
        e["duration"] = time.time() - e["started"]
        e["result_len"] = len(result)
    _render_agent_dashboard()


def _record_agent_tool(label: str, tool_name: str) -> None:
    e = _agent_registry.get(label)
    if not e:
        return
    e["tool_count"] += 1
    e["last_tool"] = tool_name
    _render_agent_dashboard()


_agent_dashboard_last_render: float = 0.0
_AGENT_DASHBOARD_THROTTLE: float = 0.25  # seconds between renders

def _render_agent_dashboard() -> None:
    """Render (or re-render) the agent progress dashboard in the REPL.

    Throttled to avoid excessive screen flickering during parallel agent runs.
    """
    global _agent_dashboard_last_render
    import time
    now = time.time()
    if now - _agent_dashboard_last_render < _AGENT_DASHBOARD_THROTTLE:
        return
    _agent_dashboard_last_render = now
    
    repl = _get_repl()
    if not repl:
        # Classic (non-REPL) mode: just print a plain status line per update
        # (could be noisy, but non-REPL is rarely used for parallel agents).
        return

    # Strip previous dashboard lines (marked with "class:agent-dash")
    repl._msg_lines[:] = [
        (s, t) for (s, t) in repl._msg_lines if s != "class:agent-dash"
    ]

    if not _agent_registry:
        repl._invalidate()
        return

    active = [e for e in _agent_registry.values() if not e["done"]]
    done = [e for e in _agent_registry.values() if e["done"]]
    total = len(_agent_registry)

    # Header
    if active:
        hdr = f"  ⇶ {len(done)}/{total} subagents done · {len(active)} running"
    else:
        hdr = f"  ✓ {total} subagents complete"
    repl._msg_lines.append(("class:agent-dash", hdr + "\n"))

    active_items = [(label, e) for label, e in _agent_registry.items() if not e["done"]]
    done_items = [(label, e) for label, e in _agent_registry.items() if e["done"]]
    max_active = 5
    max_done = 2
    rows = active_items[:max_active] + done_items[-max_done:]

    for label, e in rows:
        if e["done"]:
            icon = "✅"
            detail = f"done in {e['duration']:.1f}s · {e['tool_count']} tools"
        else:
            icon = "⏳"
            last = e["last_tool"] or "starting…"
            detail = f"{e['tool_count']} tools · current: {last}"
        row = f"     {icon} [{label}] {e['task']}  ·  {detail}\n"
        repl._msg_lines.append(("class:agent-dash", row))

    hidden_active = max(0, len(active_items) - max_active)
    hidden_done = max(0, len(done_items) - min(len(done_items), max_done))
    hidden_parts = []
    if hidden_active:
        hidden_parts.append(f"{hidden_active} more running")
    if hidden_done:
        hidden_parts.append(f"{hidden_done} completed hidden")
    if hidden_parts:
        repl._msg_lines.append(("class:agent-dash", f"     … {' · '.join(hidden_parts)}\n"))

    repl._msg_lines.append(("class:agent-dash", "\n"))
    repl._invalidate()


def agent_registry_clear() -> None:
    """Called at the end of a user turn, once all agents have synthesized."""
    _agent_registry.clear()
    repl = _get_repl()
    if repl:
        repl._msg_lines[:] = [
            (s, t) for (s, t) in repl._msg_lines if s != "class:agent-dash"
        ]
        repl._invalidate()


class _REPLConsole:
    """Proxy console that routes output to REPL message buffer when active,
    otherwise falls through to the real rich Console.

    In REPL mode we capture as **plain text** (no ANSI) to avoid the
    escape sequences corrupting the prompt_toolkit render.

    When ``_capturing`` is True, output is buffered into ``_capture_lines``
    instead of being sent to the REPL — used by ``start_capture()`` /
    ``stop_capture()`` to feed the full-screen pager.
    """

    def __init__(self) -> None:
        self._capturing: bool = False
        self._capture_lines: list[tuple[str, str]] = []

    def start_capture(self) -> None:
        """Begin buffering print() output for the pager."""
        self._capturing = True
        self._capture_lines = []

    def stop_capture(self) -> list[tuple[str, str]]:
        """Stop buffering and return captured (style, text) tuples."""
        if not self._capturing:
            return []
        self._capturing = False
        lines = self._capture_lines
        self._capture_lines = []
        return lines

    def print(self, *args: Any, **kwargs: Any) -> None:
        _sync_console_width()
        # Capture mode: buffer everything, regardless of REPL state
        if self._capturing:
            _plain_buf.truncate(0)
            _plain_buf.seek(0)
            kwargs.pop("style", None)
            kwargs.pop("highlight", None)
            try:
                _plain_console.print(*args, **kwargs)
            except Exception:
                text = " ".join(str(a) for a in args)
                _plain_buf.write(text + "\n")
            text = _safe_display_text(_plain_buf.getvalue(), max_line=420, max_total=12000)
            if text:
                self._capture_lines.append(("class:msg-info", text))
            return

        repl = _get_repl()
        if repl:
            _plain_buf.truncate(0)
            _plain_buf.seek(0)
            # Strip "style=" kwarg that only affects ANSI coloring
            kwargs.pop("style", None)
            kwargs.pop("highlight", None)
            try:
                _plain_console.print(*args, **kwargs)
            except Exception:
                # Last-resort: just str() everything
                text = " ".join(str(a) for a in args)
                _plain_buf.write(text + "\n")
            text = _plain_buf.getvalue()
            if text:
                repl.append_output(text, "class:msg-info")
        else:
            console.print(*args, **kwargs)

    def input(self, *args: Any, **kwargs: Any) -> str:
        return console.input(*args, **kwargs)


repl_console = _REPLConsole()

# Capture console: writes to StringIO so we can grab ANSI output
_capture_buf = StringIO()
_capture_console = Console(
    file=_capture_buf, theme=THEME,
    force_terminal=True, color_system="truecolor",
    width=120,
)

# Plain text capture console (no ANSI, no color) for REPL routing
_plain_buf = StringIO()
_plain_console = Console(
    file=_plain_buf, theme=THEME,
    force_terminal=False, no_color=True,
    width=120, markup=True,
)


def _sync_console_width() -> None:
    """Update capture consoles to match actual terminal width."""
    w = shutil.get_terminal_size().columns
    if w > 20:
        _plain_console.width = w
        _capture_console.width = w


def _safe_display_text(text: Any, *, max_line: int = 240, max_total: int = 4000) -> str:
    import re
    s = "" if text is None else str(text)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", s)
    s = "".join(
        ch if ch == "\n" or ch == "\t" or ord(ch) >= 32 else " "
        for ch in s
    )
    if len(s) > max_total:
        s = s[:max_total] + "…"
    lines = []
    for line in s.split("\n"):
        if len(line) > max_line:
            line = line[:max_line] + "…"
        lines.append(line)
    return "\n".join(lines)


def _get_repl():
    """Get the active REPL, if any."""
    from ccb.repl import get_active_repl
    return get_active_repl()


_fold_generation = 0
_fold_counts: dict[str, int] = {}


def _reset_repl_folds() -> None:
    global _fold_generation
    _fold_generation += 1
    _fold_counts.clear()


def _fold_marker(key: str) -> str:
    return f"class:fold-{abs(hash(key))}"


def _remove_folded_line(repl: Any, marker: str) -> bool:
    for i, (style, _text) in enumerate(repl._msg_lines):
        if style != marker:
            continue
        end = i + 1
        while end < len(repl._msg_lines) and "\n" not in repl._msg_lines[end - 1][1]:
            end += 1
        del repl._msg_lines[i:end]
        return True
    return False


def _with_fold_count(
    fragments: list[tuple[str, str]],
    count: int,
) -> list[tuple[str, str]]:
    if count <= 1:
        return fragments
    result = list(fragments)
    suffix = f" ×{count}"
    for i in range(len(result) - 1, -1, -1):
        style, text = result[i]
        if text.endswith("\n"):
            body = text[:-1]
            result[i] = (style, body)
            result.insert(i + 1, ("class:dim", suffix))
            result.insert(i + 2, (style, "\n"))
            return result
    result.append(("class:dim", suffix))
    result.append(("", "\n"))
    return result


def _append_folded_fragments(
    repl: Any,
    key: str,
    fragments: list[tuple[str, str]],
) -> None:
    scoped_key = f"{id(repl)}:{_fold_generation}:{key}"
    marker = _fold_marker(scoped_key)
    found = _remove_folded_line(repl, marker)
    count = _fold_counts.get(scoped_key, 0) + 1 if found else 1
    _fold_counts[scoped_key] = count
    repl._msg_lines.append((marker, ""))
    repl._msg_lines.extend(_with_fold_count(fragments, count))
    repl._scroll_back = 0
    repl._invalidate()


def _normalize_info_key(msg: str) -> str:
    import re
    lowered = msg.lower()
    if "rate limit" in lowered or "rate limited" in lowered:
        return "rate-limit"
    if "retrying in" in lowered or "retrying" in lowered:
        return "retry"
    return re.sub(r"\d+(?:\.\d+)?s", "#s", msg.strip())


def _rich_capture(renderable: Any) -> str:
    """Render a rich object to ANSI string."""
    _capture_buf.truncate(0)
    _capture_buf.seek(0)
    _capture_console.print(renderable, highlight=False)
    return _capture_buf.getvalue()


def _out(text: str = "", style: str = "", *, rich_obj: Any = None) -> None:
    """Output text, routing to REPL buffer or console as appropriate."""
    _sync_console_width()
    # Capture mode: buffer for pager regardless of REPL state
    if repl_console._capturing:
        if rich_obj is not None:
            _plain_buf.truncate(0)
            _plain_buf.seek(0)
            _plain_console.print(rich_obj)
            text = _safe_display_text(_plain_buf.getvalue(), max_line=420, max_total=12000)
            if text:
                repl_console._capture_lines.append((style or "class:msg-info", text))
        elif text:
            repl_console._capture_lines.append((style or "class:msg-info", _safe_display_text(text)))
        return

    repl = _get_repl()
    if repl:
        if rich_obj is not None:
            ansi = _rich_capture(rich_obj)
            repl.append_ansi(ansi)
        elif text:
            repl.append_output(text + "\n", style)
    else:
        if rich_obj is not None:
            console.print(rich_obj)
        elif text:
            if style:
                console.print(f"[{style}]{text}[/{style}]")
            else:
                console.print(text)


def print_banner(version: str, model: str, cwd: str) -> None:
    """Print startup logo card, matching official CondensedLogo style."""
    repl = _get_repl()
    if repl:
        return  # REPL has its own status bar
    # ── Card layout matching official CondensedLogo ──
    width = console.width - 4
    rule = "─" * max(8, width - 4)
    inner = Text()
    inner.append("Claude Code", style=f"bold {CLAUDE_ORANGE}")
    inner.append(f"  v{version}\n", style="dim")
    inner.append(f"{rule}\n", style="dim")
    inner.append("model ", style="dim")
    inner.append(f"{model}\n")
    inner.append("cwd   ", style="dim")
    inner.append(cwd)
    console.print(Panel(
        inner,
        border_style="#333333",
        box=__import__('rich.box', fromlist=['ROUNDED']).ROUNDED,
        expand=False,
        width=width,
    ))
    console.print()


def _md_to_ptk(md_text: str) -> list[tuple[str, str]]:
    """Convert markdown text to prompt_toolkit formatted-text fragments.

    Lightweight parser — handles headings, bold, inline code, code blocks,
    bullet lists, and paragraphs. No external dependency needed.
    """
    import re

    frags: list[tuple[str, str]] = []
    lines = md_text.strip().split("\n")
    in_code_block = False
    code_buf: list[str] = []
    prev_blank = False  # track consecutive blank lines

    for line in lines:
        # Code block fences
        if line.strip().startswith("```"):
            if in_code_block:
                # End of code block
                if code_buf:
                    frags.append(("class:md-code-block", "\n".join(code_buf) + "\n"))
                code_buf = []
                in_code_block = False
            else:
                in_code_block = True
            prev_blank = False
            continue

        if in_code_block:
            code_buf.append(line)
            continue

        # Blank line — collapse consecutive blanks into at most one
        if not line.strip():
            if prev_blank:
                continue  # skip consecutive blank
            prev_blank = True
            frags.append(("", "\n"))
            continue
        prev_blank = False

        # Headings
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            frags.append(("class:md-heading", f"{m.group(2)}\n"))
            continue

        # Bullet lists
        m = re.match(r"^(\s*)[-*+]\s+(.*)", line)
        if m:
            indent = m.group(1)
            frags.append(("", f"{indent} • "))
            _parse_inline(m.group(2), frags)
            frags.append(("", "\n"))
            continue

        # Numbered lists
        m = re.match(r"^(\s*)\d+[.)]\s+(.*)", line)
        if m:
            indent = m.group(1)
            frags.append(("", f"{indent}   "))
            _parse_inline(m.group(2), frags)
            frags.append(("", "\n"))
            continue

        # Regular paragraph line
        _parse_inline(line, frags)
        frags.append(("", "\n"))

    # Close unclosed code block
    if in_code_block and code_buf:
        frags.append(("class:md-code-block", "\n".join(code_buf) + "\n"))

    return frags


def _apply_left_border(
    frags: list[tuple[str, str]],
    border_style: str,
    border_char: str = "  ┃ ",
) -> list[tuple[str, str]]:
    """Wrap fragments with a left-border prefix that fires ONCE per actual line.

    Naive per-fragment wrapping breaks inline formatting (e.g. `code` inside
    a paragraph) onto its own line with a new border. This groups fragments
    by the newlines that actually exist in their text, preserving inline
    flow while still drawing the border continuously on every line (blank
    included, matching Ink's borderLeft behavior).
    """
    result: list[tuple[str, str]] = []
    current: list[tuple[str, str]] = []
    for style, text in frags:
        if not text:
            continue
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if part:
                current.append((style, part))
            if i < len(parts) - 1:
                # End of a line — emit border + accumulated content + newline
                if current:
                    result.append((border_style, border_char))
                    result.extend(current)
                else:
                    # Blank line — leave it empty instead of drawing chrome
                    pass
                result.append(("", "\n"))
                current = []
    # Flush trailing partial line (no terminating \n in original)
    if current:
        result.append((border_style, border_char))
        result.extend(current)
        result.append(("", "\n"))
    return result


# Module-level link target registry: display_text → url
# Used by [text](url) markdown links where display differs from target.
_link_targets: dict[str, str] = {}


def _parse_inline(text: str, frags: list[tuple[str, str]]) -> None:
    """Parse inline markdown: **bold**, `code`, *italic*, [text](url), bare URLs."""
    import re
    # Order matters: markdown links before bold (both use [...]), bare URLs last
    pattern = re.compile(
        r'(\[([^\]]+)\]\(([^)]+)\)'   # [text](url)
        r'|\*\*(.+?)\*\*'              # **bold**
        r'|`(.+?)`'                       # `code`
        r'|(?<!\w)\*([^*\n]+?)\*(?!\w)'  # *italic*
        r'|(https?://[^\s<>\)\]]+)'     # bare URL
        r')'
    )
    pos = 0
    for m in pattern.finditer(text):
        # Text before match
        if m.start() > pos:
            frags.append(("", text[pos:m.start()]))
        if m.group(2) and m.group(3):     # [text](url)
            display = m.group(2)
            url = m.group(3)
            frags.append(("class:md-link", display))
            _link_targets[display] = url
        elif m.group(4):                   # **bold**
            frags.append(("class:md-bold", m.group(4)))
        elif m.group(5):                   # `code`
            frags.append(("class:md-code", m.group(5)))
        elif m.group(6):                   # *italic*
            frags.append(("class:md-italic", m.group(6)))
        elif m.group(7):                   # bare URL
            url = m.group(7)
            frags.append(("class:md-link", url))
            _link_targets[url] = url
        pos = m.end()
    # Remaining text
    if pos < len(text):
        frags.append(("", text[pos:]))


def _short_name(name: Any, max_len: int = 34) -> str:
    s = _safe_display_text(name or "", max_line=max_len, max_total=max_len)
    if len(s) <= max_len:
        return s
    keep = max(8, max_len - 2)
    return "…" + s[-keep:]


def attachment_fragments(
    *,
    at_files: list[dict[str, Any]] | None = None,
    images: list[dict[str, Any]] | None = None,
    files: list[dict[str, Any]] | None = None,
    media: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str]]:
    frags: list[tuple[str, str]] = []

    def _line(icon: str, label: str, items: list[dict[str, Any]] | None) -> None:
        if not items:
            return
        names = [_short_name(item.get("filename") or item.get("path") or label) for item in items]
        shown = ", ".join(names[:3])
        hidden = len(names) - 3
        suffix = f", +{hidden} more" if hidden > 0 else ""
        frags.append(("class:msg-info", f"  {icon} {len(items)} {label}(s): {shown}{suffix}\n"))

    _line("📄", "@-mentioned file", at_files)
    _line("📎", "image", images)
    _line("📄", "file", files)
    _line("🎬", "media", media)
    return frags


def print_user_message(text: str) -> None:
    """Print user message with a simple label and indented body."""
    repl = _get_repl()
    if repl:
        _reset_repl_folds()
        repl._msg_lines.append(("", "\n"))
        repl._msg_lines.append(("class:msg-user-label", "  You\n"))
        body = text + "\n" if not text.endswith("\n") else text
        for line in body.splitlines():
            repl._msg_lines.append(("class:msg-user", f"    {line}\n"))
        repl._invalidate()
        return
    # Classic mode: blue left border via Rich columns
    console.print()
    console.print("  [label.you]🧑 You[/label.you]")
    for line in text.split("\n"):
        row = Text("  ┃ ", style="user")
        row.append(_safe_display_text(line, max_line=240, max_total=500))
        console.print(row, highlight=False)


def print_assistant_text(text: str) -> None:
    """Print completed assistant message with a simple label and indented body."""
    if not text or not text.strip():
        return
    repl = _get_repl()
    if repl:
        repl._msg_lines.append(("", "\n"))
        repl._msg_lines.append(("class:msg-assistant-label", "  Assistant\n"))
        md_frags = _md_to_ptk(text)
        repl._msg_lines.extend(_apply_left_border(md_frags, "class:msg-assistant-border", border_char="    "))
        repl._invalidate()
        return
    # Classic mode: orange left border + markdown (via Table for stable alignment)
    from rich.table import Table
    console.print()
    console.print("  [label.claude]🤖 Claude[/label.claude]")
    t = Table(show_header=False, show_edge=False, box=None, padding=0, expand=True)
    t.add_column(width=4, no_wrap=True)
    t.add_column(ratio=1)
    t.add_row(Text("  ┃ ", style=f"{CLAUDE_ORANGE}"), Markdown(text))
    console.print(t)


_TOOL_VERB_MAP: dict[str, str] = {
    "file_read": "Reading",
    "file_write": "Writing",
    "file_edit": "Editing",
    "bash": "Running",
    "grep": "Searching",
    "glob": "Searching",
    "web_fetch": "Fetching",
    "web_search": "Searching",
    "agent": "Delegating",
    "notebook_edit": "Editing",
    "todo_write": "Planning",
}


def print_tool_call(name: str, input_data: dict[str, Any]) -> None:
    """Print tool invocation with ⏺ dot + bold name + (summary)."""
    # Silence per-tool output inside a subagent — the parent REPL renders a
    # single rolling progress line for each concurrent agent instead.
    try:
        from ccb.agent_context import is_inside_agent, current_agent_label
        if is_inside_agent():
            _record_agent_tool(current_agent_label(), name)
            return
    except Exception:
        pass
    summary = _summarize_tool_input(name, input_data)
    summary = _safe_display_text(summary, max_line=160, max_total=300)
    safe_name = _safe_display_text(name, max_line=80, max_total=80)
    repl = _get_repl()
    if repl:
        # Update spinner verb so footer shows what's happening
        repl._spinner_verb = _TOOL_VERB_MAP.get(name, "Working")
        fragments = [
            ("class:msg-tool-dot", "  ⏺ "),
            ("class:msg-tool-name", f"{safe_name}"),
        ]
        if summary:
            fragments.append(("class:msg-tool-summary", f" ({summary})"))
        fragments.append(("", "\n"))
        _append_folded_fragments(repl, f"tool-call:{safe_name}:{summary}", fragments)
        return
    line = Text("  ⏺ ", style="tool.dot")
    line.append(safe_name, style="tool")
    if summary:
        line.append(f"  {summary}", style="tool.dim")
    console.print(line, highlight=False)


def _summarize_tool_result(
    name: str,
    input_data: dict[str, Any] | None,
    output: str,
    is_error: bool = False,
) -> str:
    """Collapse a tool's raw output to a one-line summary.

    Mirrors the official Claude Code behaviour: after execution, tool results
    are condensed to a single `MessageResponse height={1}` line — full output
    goes only to the model, never to the user's visible transcript.
    """
    import os
    input_data = input_data or {}

    # Errors: show a short truncated error message instead of a summary
    if is_error:
        first_line = (output.strip().splitlines() or [""])[0]
        return first_line[:200] + ("…" if len(first_line) > 200 else "")

    def _disp_path(p: str) -> str:
        """Shorten long paths to ~/foo/bar.py for readability."""
        if not p:
            return ""
        home = os.path.expanduser("~")
        if p.startswith(home):
            p = "~" + p[len(home):]
        return p

    n_lines = output.count("\n") + (1 if output and not output.endswith("\n") else 0)
    n_chars = len(output)

    if name == "file_read":
        path = _disp_path(input_data.get("file_path", ""))
        return f"Read {n_lines} {'line' if n_lines == 1 else 'lines'}" + (
            f" · {path}" if path else ""
        )
    if name == "file_write":
        path = _disp_path(input_data.get("file_path", ""))
        content = input_data.get("content", "")
        wrote_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"Wrote {wrote_lines} {'line' if wrote_lines == 1 else 'lines'}" + (
            f" · {path}" if path else ""
        )
    if name == "file_edit":
        path = _disp_path(input_data.get("file_path", ""))
        # Parse +N/-N from tool output (e.g. "/path/file.py +5 -3")
        import re
        add_m = re.search(r'\+(\d+)', output)
        rem_m = re.search(r'-(\d+)', output)
        n_add = int(add_m.group(1)) if add_m else 0
        n_rem = int(rem_m.group(1)) if rem_m else 0
        if output.startswith("Created"):
            return f"Created {path} (+{n_add} lines)" if path else output
        parts_edit = []
        if n_add > 0:
            parts_edit.append(f"Added {n_add} {'line' if n_add == 1 else 'lines'}")
        if n_rem > 0:
            parts_edit.append(f"removed {n_rem} {'line' if n_rem == 1 else 'lines'}")
        diff_str = ", ".join(parts_edit) if parts_edit else "Edited"
        return f"{diff_str} · {path}" if path else diff_str
    if name == "grep":
        # grep returns "path:lineno:content" lines
        stripped = output.strip()
        count = stripped.count("\n") + 1 if stripped else 0
        if stripped.startswith("No files found") or count == 0:
            return "No matches"
        return f"Found {count} {'match' if count == 1 else 'matches'}"
    if name == "glob":
        stripped = output.strip()
        count = stripped.count("\n") + 1 if stripped else 0
        if count == 0 or stripped.startswith("No files"):
            return "No files"
        return f"Found {count} {'file' if count == 1 else 'files'}"
    if name == "bash":
        cmd = input_data.get("command", "")
        brief = cmd.split("\n", 1)[0][:60]
        suffix = f" ({n_lines} {'line' if n_lines == 1 else 'lines'})" if n_lines > 1 else ""
        return f"`{brief}`{suffix}"
    if name == "web_fetch":
        url = input_data.get("url", "")
        # show just the host
        from urllib.parse import urlparse
        host = urlparse(url).netloc or url
        return f"Fetched {host}"
    if name == "web_search":
        query = input_data.get("query", "")
        return f"Searched: {query[:60]}" + ("…" if len(query) > 60 else "")
    if name == "todo_write":
        todos = input_data.get("todos", [])
        n = len(todos) if isinstance(todos, list) else 0
        return f"Updated {n} {'todo' if n == 1 else 'todos'}"
    if name == "agent":
        # Agent returns its final response — keep a short preview
        first_line = (output.strip().splitlines() or [""])[0]
        return first_line[:120] + ("…" if len(first_line) > 120 else "")
    if name == "notebook_edit":
        path = _disp_path(input_data.get("notebook_path", ""))
        return f"Edited notebook {path}" if path else "Edited notebook"
    if name == "ask_user_question":
        return "Asked user"
    if name in ("enter_plan_mode", "exit_plan_mode"):
        return name.replace("_", " ").capitalize()
    if name in ("task_stop", "task_output", "list_mcp_resources", "read_mcp_resource"):
        return f"{name.replace('_', ' ')} · {n_lines} {'line' if n_lines == 1 else 'lines'}"

    # Generic fallback
    if n_lines == 0:
        return "Done" if not output else f"{n_chars} chars"
    return f"{n_lines} {'line' if n_lines == 1 else 'lines'}"


def print_tool_result(
    name: str,
    output: str,
    is_error: bool = False,
    input_data: dict[str, Any] | None = None,
) -> None:
    """Print tool result as a collapsed 1-line summary (official style).

    The full output is preserved in the session for the model — this only
    controls what the user sees. Errors show a truncated error message.
    """
    # Inside a subagent: swallow output (the parent REPL shows progress via
    # its rolling dashboard).
    try:
        from ccb.agent_context import is_inside_agent
        if is_inside_agent():
            return
    except Exception:
        pass
    summary = _summarize_tool_result(name, input_data, output, is_error)
    summary = _safe_display_text(summary, max_line=220, max_total=600)
    repl = _get_repl()
    if repl:
        style = "class:msg-error" if is_error else "class:msg-tool-output"
        fragments = [
            ("class:msg-border", "  │ "),
            (style, f"{summary}\n"),
        ]
        if is_error:
            repl._msg_lines.extend(fragments)
            repl._invalidate()
        else:
            _append_folded_fragments(repl, f"tool-result:{name}:{summary}", fragments)
        return
    style = "error" if is_error else "dim"
    line = Text("  │ ", style="border")
    line.append(summary, style=style)
    console.print(line, highlight=False)


def print_error(msg: str) -> None:
    msg = _safe_display_text(msg, max_line=220, max_total=1200)
    repl = _get_repl()
    if repl:
        repl.append_output(f"  [✗] {msg}\n", "class:msg-error")
        return
    console.print(Text(f"  ✗ {msg}", style="error"))


def print_info(msg: str) -> None:
    msg = _safe_display_text(msg, max_line=220, max_total=1200)
    repl = _get_repl()
    if repl:
        _append_folded_fragments(
            repl,
            f"info:{_normalize_info_key(msg)}",
            [("class:msg-info", f"  {msg}\n")],
        )
        return
    console.print(Text(msg, style="dim"))


def print_usage(
    usage: dict[str, int],
    thinking_duration_ms: float = 0.0,
) -> None:
    from ccb.cost_tracker import format_tokens, format_cost, format_duration, get_cost_state
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cost = get_cost_state()

    # Build parts: tokens · cost · thinking duration · turn duration
    parts = [f"{format_tokens(inp)} in / {format_tokens(out)} out"]
    if cost.total_cost_usd > 0:
        parts.append(format_cost(cost.total_cost_usd))
    if thinking_duration_ms > 500:
        parts.append(f"thought {format_duration(thinking_duration_ms)}")
    if cost.last_turn_duration_ms and cost.last_turn_duration_ms > 500:
        parts.append(f"turn {format_duration(cost.last_turn_duration_ms)}")
    line = "  " + " · ".join(parts)

    repl = _get_repl()
    if repl:
        repl.append_output(line + "\n", "class:dim")
        return
    console.print(f"  [dim]{line.strip()}[/dim]")


class StreamPrinter:
    """Accumulates streaming text and renders it live.

    In REPL mode, updates the message area directly.
    In classic mode, uses Rich Live.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._thinking_buf = ""
        self._thinking_start: float = 0.0
        self._live: Live | None = None
        self._repl = None

    def start(self) -> None:
        self._buffer = ""
        self._thinking_buf = ""
        self._repl = _get_repl()
        if self._repl:
            self._repl._msg_lines.append(("", "\n"))
            self._repl._msg_lines.append(("class:msg-assistant-label", "  Assistant\n"))
            self._repl._invalidate()
            return
        # Classic mode: show label first, then live-update content
        console.print()
        console.print("  [label.claude]🤖 Claude[/label.claude]")
        self._live = Live(
            Text(""),
            console=console,
            refresh_per_second=12,
            vertical_overflow="visible",
            transient=False,
        )
        self._live.start()

    def feed(self, text: str) -> None:
        self._buffer += text
        if self._repl:
            self._repl.set_streaming(self._buffer)
            return
        if self._live:
            try:
                # Orange left border with Markdown content
                from rich.table import Table
                t = Table(show_header=False, show_edge=False, box=None, padding=0, expand=True)
                t.add_column(width=4, no_wrap=True)
                t.add_column(ratio=1)
                border_text = Text("  ┃ ", style=f"{CLAUDE_ORANGE}")
                t.add_row(border_text, Markdown(self._buffer))
                self._live.update(t)
            except Exception:
                self._live.update(Text(self._buffer))

    def feed_thinking(self, text: str) -> None:
        """Feed thinking/reasoning text (displayed dimmed)."""
        import re as _re
        import time as _time
        # Strip thinking_mode tags (may use unicode math chars from some APIs)
        text = _re.sub(r'<[^>]*thinking_mode[^>]*>.*?</[^>]*thinking_mode[^>]*>', '', text, flags=_re.DOTALL)
        if not text.strip():
            return
        if not self._thinking_buf:
            self._thinking_start = _time.time()
        self._thinking_buf += text

        # Elapsed thinking time
        elapsed = _time.time() - self._thinking_start if self._thinking_start else 0
        from ccb.cost_tracker import format_duration
        elapsed_str = f" ({format_duration(elapsed * 1000)})" if elapsed > 1.0 else ""

        if self._repl:
            snippet = self._thinking_buf[-200:]
            if len(self._thinking_buf) > 200:
                snippet = "…" + snippet
            self._repl.set_streaming(f"💭 Thinking…{elapsed_str}\n{snippet}")
            return
        if self._live:
            try:
                parts = Text()
                parts.append(f"💭 Thinking…{elapsed_str}\n", style="dim italic")
                snippet = self._thinking_buf[-300:]
                if len(self._thinking_buf) > 300:
                    snippet = "…" + snippet
                parts.append(snippet, style="dim")
                from rich.table import Table
                t = Table(show_header=False, show_edge=False, box=None, padding=0, expand=True)
                t.add_column(width=4, no_wrap=True)
                t.add_column(ratio=1)
                t.add_row(Text("  ┃ ", style="dim"), parts)
                self._live.update(t)
            except Exception:
                pass

    def stop(self) -> str:
        if self._repl:
            self._repl.finish_streaming()
            # Show "Thought for X.Xs" line if thinking was used
            if self._thinking_buf and self._thinking_start:
                import time as _time
                from ccb.cost_tracker import format_duration
                dur = (_time.time() - self._thinking_start) * 1000
                if dur > 500:
                    self._repl._msg_lines.append(
                        ("class:dim", f"  💭 Thought for {format_duration(dur)}\n")
                    )
            if self._buffer.strip():
                md_frags = _md_to_ptk(self._buffer)
                bordered = _apply_left_border(md_frags, "class:msg-assistant-border")
                self._repl._msg_lines.extend(bordered)
                self._repl._invalidate()
            self._repl = None
        elif self._live:
            if self._buffer.strip():
                try:
                    from rich.table import Table
                    t = Table(show_header=False, show_edge=False, box=None, padding=0, expand=True)
                    t.add_column(width=4, no_wrap=True)
                    t.add_column(ratio=1)
                    t.add_row(Text("  ┃ ", style=f"{CLAUDE_ORANGE}"), Markdown(self._buffer))
                    self._live.update(t)
                except Exception:
                    pass
            self._live.stop()
            self._live = None
        result = self._buffer
        self._buffer = ""
        return result


async def ask_permission(tool_name: str, input_data: dict[str, Any]) -> str:
    """Ask user for permission to execute a tool.

    Returns an ApprovalChoice string:
      allow_once, allow_session, allow_workspace, deny_once, deny_workspace
    """
    from ccb.permissions import ApprovalChoice

    repl = _get_repl()
    if repl:
        from ccb.permissions import _bypass_all
        if _bypass_all:
            return "allow_once"
        summary = _summarize_tool_input(tool_name, input_data)
        summary = _safe_display_text(summary, max_line=160, max_total=300)
        safe_tool = _safe_display_text(tool_name, max_line=80, max_total=80)
        repl.append_output(f"  {safe_tool}  {summary}\n", "class:msg-tool-name")
        try:
            ok = await repl.ask_permission_async(safe_tool, summary)
            return "allow_session" if ok else "deny_once"
        except Exception:
            repl.append_output("  (auto-approved - permission dialog failed)\n", "class:dim")
            return "allow_once"

    summary = _safe_display_text(_summarize_tool_input(tool_name, input_data), max_line=160, max_total=300)
    safe_tool = _safe_display_text(tool_name, max_line=80, max_total=80)
    line = Text("\n  ⏺ ", style="tool.dot")
    line.append(safe_tool, style="tool")
    if summary:
        line.append(f"  {summary}", style="tool.dim")
    console.print(line, highlight=False)
    console.print("  [dim]│[/dim] [dim]1.[/dim] Allow once    [dim]2.[/dim] Allow session   [dim]3.[/dim] Always allow workspace")
    console.print("  [dim]│[/dim] [dim]4.[/dim] Deny once     [dim]5.[/dim] Always deny workspace")
    try:
        response = console.input("  [dim]│ >[/dim] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "deny_once"

    choice_map: dict[str, ApprovalChoice] = {
        "": "allow_once", "1": "allow_once", "y": "allow_once", "yes": "allow_once",
        "2": "allow_session", "s": "allow_session", "session": "allow_session",
        "a": "allow_session",  # legacy "a" = always for session
        "3": "allow_workspace", "w": "allow_workspace", "always": "allow_workspace",
        "4": "deny_once", "n": "deny_once", "no": "deny_once",
        "5": "deny_workspace", "d": "deny_workspace",
    }
    return choice_map.get(response, "deny_once")


def _summarize_tool_input(name: str, data: dict[str, Any]) -> str:
    """Create a short summary of tool input for display."""
    if name == "bash":
        return f"`{data.get('command', '')[:100]}`"
    if name == "file_read":
        return data.get("file_path", "")
    if name == "file_write":
        return f"{data.get('file_path', '')} ({len(data.get('content', ''))} chars)"
    if name == "file_edit":
        return data.get("file_path", "")
    if name == "grep":
        return f"'{data.get('pattern', '')}' in {data.get('path', '.')}"
    if name == "glob":
        return f"'{data.get('pattern', '')}'"
    if name == "agent":
        task = data.get("task", "")
        return task[:80] + ("..." if len(task) > 80 else "")
    if name == "ask_user_question":
        q = data.get("question", "")
        opts = data.get("options", "")
        n_opts = 0
        if isinstance(opts, str):
            n_opts = len([o.strip() for o in opts.split(",") if o.strip()])
        elif isinstance(opts, list):
            for item in opts:
                if isinstance(item, dict):
                    label = str(item.get("label") or item.get("value") or item.get("description") or "").strip()
                    if label:
                        n_opts += 1
                elif item is not None and str(item).strip():
                    n_opts += 1
        summary = q[:60] + ("..." if len(q) > 60 else "")
        if n_opts:
            summary += f" ({n_opts} options)"
        return summary
    # Generic fallback
    import json
    return json.dumps(data, ensure_ascii=False)[:100]
