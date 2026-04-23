"""Rich-based terminal display for streaming, tool output, and messages.

In full-screen REPL mode (repl.py), output is routed to the REPL's message
buffer instead of printing directly to stderr.  The helper ``_out()`` checks
for an active REPL and delegates accordingly.
"""
from __future__ import annotations

import sys
from io import StringIO
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.theme import Theme

# Official Claude Code color palette (dark terminal)
CLAUDE_ORANGE = "#d77757"       # rgb(215,119,87) — brand orange
LABEL_BLUE = "#2563eb"          # rgb(37,99,235)  — "You" label
LABEL_ORANGE = "#d77757"        # same as brand   — "Claude" label
BORDER_DIM = "#555555"           # │ prefix for tool results
BG_USER = "#1a1a2e"             # user message background tint

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


def _render_agent_dashboard() -> None:
    """Render (or re-render) the agent progress dashboard in the REPL.

    We replace the dashboard in place by keeping a marker index into the
    REPL's _msg_lines. On each update we rewrite the block from the marker
    to the end — this keeps the display a "live" status panel that sits at
    the bottom of the scrollback while agents are running.

    Strategy: tag each dashboard-owned line with a sentinel style so we can
    find & strip the previous render. Simpler than index tracking.
    """
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

    # Per-agent rows
    for label, e in _agent_registry.items():
        if e["done"]:
            icon = "✅"
            detail = f"done in {e['duration']:.1f}s · {e['tool_count']} tools"
        else:
            icon = "⏳"
            last = e["last_tool"] or "starting…"
            detail = f"{e['tool_count']} tools · current: {last}"
        row = f"     {icon} [{label}] {e['task']}  ·  {detail}\n"
        repl._msg_lines.append(("class:agent-dash", row))

    repl._msg_lines.append(("class:agent-dash", "\n"))
    repl._invalidate()


def agent_registry_clear() -> None:
    """Called at the end of a user turn, once all agents have synthesized."""
    _agent_registry.clear()


class _REPLConsole:
    """Proxy console that routes output to REPL message buffer when active,
    otherwise falls through to the real rich Console.

    In REPL mode we capture as **plain text** (no ANSI) to avoid the
    escape sequences corrupting the prompt_toolkit render.
    """

    def print(self, *args: Any, **kwargs: Any) -> None:
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


def _get_repl():
    """Get the active REPL, if any."""
    from ccb.repl import get_active_repl
    return get_active_repl()


def _rich_capture(renderable: Any) -> str:
    """Render a rich object to ANSI string."""
    _capture_buf.truncate(0)
    _capture_buf.seek(0)
    _capture_console.print(renderable, highlight=False)
    return _capture_buf.getvalue()


def _out(text: str = "", style: str = "", *, rich_obj: Any = None) -> None:
    """Output text, routing to REPL buffer or console as appropriate."""
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
    width = min(console.width - 4, 74)
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
    lines = md_text.split("\n")
    in_code_block = False
    code_buf: list[str] = []

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
            continue

        if in_code_block:
            code_buf.append(line)
            continue

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

        # Blank line
        if not line.strip():
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
                result.append((border_style, border_char))
                result.extend(current)
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
        r'|\*(.+?)\*'                    # *italic*
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


def print_user_message(text: str) -> None:
    """Print user message with blue left border + 🧑 You label."""
    repl = _get_repl()
    if repl:
        repl._msg_lines.append(("", "\n"))
        repl._msg_lines.append(("class:msg-user-label", "  🧑 You\n"))
        bordered = _apply_left_border(
            [("class:msg-user", text + "\n" if not text.endswith("\n") else text)],
            "class:msg-user-border",
        )
        repl._msg_lines.extend(bordered)
        repl._invalidate()
        return
    # Classic mode: blue left border via Rich columns
    console.print()
    console.print(f"  [label.you]🧑 You[/label.you]")
    for line in text.split("\n"):
        console.print(f"  [user]┃[/user] {line}")


def print_assistant_text(text: str) -> None:
    """Print completed assistant message with orange left border + 🤖 Claude label."""
    repl = _get_repl()
    if repl:
        repl._msg_lines.append(("", "\n"))
        repl._msg_lines.append(("class:msg-assistant-label", "  🤖 Claude\n"))
        md_frags = _md_to_ptk(text)
        bordered = _apply_left_border(md_frags, "class:msg-assistant-border")
        repl._msg_lines.extend(bordered)
        repl._invalidate()
        return
    # Classic mode: orange left border + markdown (via Table for stable alignment)
    from rich.table import Table
    console.print()
    console.print(f"  [label.claude]🤖 Claude[/label.claude]")
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
    repl = _get_repl()
    if repl:
        # Update spinner verb so footer shows what's happening
        repl._spinner_verb = _TOOL_VERB_MAP.get(name, "Working")
        repl._msg_lines.append(("class:msg-tool-dot", "  ⏺ "))
        repl._msg_lines.append(("class:msg-tool-name", f"{name}"))
        if summary:
            repl._msg_lines.append(("class:msg-tool-summary", f" ({summary})"))
        repl._msg_lines.append(("", "\n"))
        repl._invalidate()
        return
    console.print(f"  [tool.dot]⏺[/tool.dot] [tool]{name}[/tool]  {summary}", highlight=False)


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
    repl = _get_repl()
    if repl:
        style = "class:msg-error" if is_error else "class:msg-tool-output"
        repl._msg_lines.append(("class:msg-border", "  │ "))
        repl._msg_lines.append((style, f"{summary}\n"))
        repl._invalidate()
        return
    style = "error" if is_error else "dim"
    console.print(
        f"  [border]│[/border] [{style}]{summary}[/{style}]",
        highlight=False,
    )


def print_error(msg: str) -> None:
    repl = _get_repl()
    if repl:
        repl.append_output(f"  [✗] {msg}\n", "class:msg-error")
        return
    console.print(f"  [error]✗ {msg}[/error]")


def print_info(msg: str) -> None:
    repl = _get_repl()
    if repl:
        repl.append_output(f"  {msg}\n", "class:msg-info")
        return
    console.print(f"[dim]{msg}[/dim]")


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
            self._repl._msg_lines.append(("class:msg-assistant-label", "  🤖 Claude\n"))
            self._repl._invalidate()
            return
        # Classic mode: show label first, then live-update content
        console.print()
        console.print(f"  [label.claude]🤖 Claude[/label.claude]")
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
        import time as _time
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
        repl.append_output(f"  {tool_name}  {summary}\n", "class:msg-tool-name")
        try:
            ok = await repl.ask_permission_async(tool_name, summary)
            return "allow_session" if ok else "deny_once"
        except Exception:
            repl.append_output("  (auto-approved - permission dialog failed)\n", "class:dim")
            return "allow_once"

    summary = _summarize_tool_input(tool_name, input_data)
    console.print(f"\n  [tool.dot]⏺[/tool.dot] [tool]{tool_name}[/tool]  {summary}")
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
    # Generic fallback
    import json
    return json.dumps(data, ensure_ascii=False)[:100]
