"""Full-screen REPL application matching the original CLI's interactive UI.

Layout (messages grow upward from the input line):
  ┌─────────────────────────────────┐
  │ ✦ CCB v0.1.0 · model · acct   │  status bar (2 lines)
  ├─────────────────────────────────┤
  │            (spacer)             │  ← flex: pushes msgs down
  │                                 │
  │ ❯ 你好                         │  ← messages sit just above input
  │ 🤖 你好！有什么可以帮你的？      │
  │ tokens: 0 in / 0 out           │
  ├─────────────────────────────────┤
  │ ❯ _                            │  input
  │ ? for help · esc+enter newline │  footer
  └─────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from typing import Any

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion, WordCompleter
from prompt_toolkit.formatted_text import ANSI as PTK_ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.styles import Style

# Slash commands with short descriptions — mirrors the official Claude Code
# completion menu. Ordered by category so `/` without a query shows a
# predictable, logical layout instead of alphabetical noise.
SLASH_COMMAND_DESCRIPTIONS: dict[str, str] = {
    # Core
    "/help": "Show commands and shortcuts",
    "/clear": "Clear conversation and screen (Ctrl+L)",
    "/compact": "Compact conversation to free context",
    "/exit": "Exit ccb (Ctrl+D)",
    "/quit": "Exit ccb",
    "/q": "Exit ccb (alias)",
    # Model / account
    "/model": "Switch or view the active model",
    "/account": "Manage accounts (add / remove / list / switch)",
    "/login": "Sign in to an account",
    "/logout": "Sign out of current account",
    "/config": "Open config settings",
    "/effort": "Set reasoning effort (low/medium/high)",
    "/fast": "Toggle fast mode",
    "/thinking": "Toggle extended thinking",
    # Context / usage
    "/context": "Show context window usage",
    "/files": "List files loaded into context",
    "/cost": "Show token cost estimate",
    "/usage": "Show usage stats",
    "/budget": "View / set token budget",
    "/status": "Show session status",
    "/stats": "Show detailed statistics",
    "/summary": "Summarize current conversation",
    "/history": "Show message history",
    # Sessions
    "/sessions": "List all saved sessions",
    "/resume": "Resume a prior session",
    "/continue": "Continue the last session",
    "/session": "Session management",
    "/rename": "Rename current session",
    "/tag": "Tag current session",
    "/share": "Share session output",
    # Git
    "/diff": "Show git diff",
    "/branch": "Show git branches",
    "/commit": "Create a git commit",
    "/undo": "Undo last change",
    "/redo": "Redo last undone change",
    "/checkpoint": "Create a checkpoint",
    "/restore": "Restore from checkpoint",
    "/rewind": "Rewind conversation",
    # Memory / workspace
    "/memory": "Manage project memory",
    "/init": "Initialize CLAUDE.md",
    "/add-dir": "Add directory to context",
    # Image / file upload
    "/image": "Attach an image to next message",
    "/file": "Attach a text file to next message",
    # Tools / MCP
    "/mcp": "Manage MCP servers",
    "/doctor": "Run diagnostics",
    "/permissions": "Manage tool permissions",
    "/hooks": "Manage hooks",
    "/plan": "Enter plan mode",
    "/tasks": "Show active tasks",
    "/agents": "Manage sub-agents",
    # UI / preferences
    "/vim": "Toggle vim mode",
    "/theme": "Change color theme",
    "/color": "Toggle color output",
    "/output-style": "Change output format",
    "/keybindings": "Show key bindings",
    "/stickers": "Request Claude stickers",
    # Copy / export
    "/copy": "Copy last message to clipboard",
    "/export": "Export session (json/md/html)",
    # Git
    "/commit": "Auto-generate commit message and commit",
    "/diff": "Show git diff (staged/unstaged)",
    "/branch": "List/switch/create branches",
    "/undo": "Undo last commit (soft reset)",
    "/redo": "Redo (stash pop)",
    # GitHub
    "/pr-comments": "Show PR review comments",
    "/review": "AI code review of current PR",
    "/autofix-pr": "Auto-fix PR review comments",
    "/issue": "List/view GitHub issues",
    "/install-github-app": "Install ccb GitHub App",
    "/install-slack-app": "Slack integration",
    # Platform
    "/feedback": "Send feedback",
    "/btw": "Share terminal setup info",
    "/privacy-settings": "Open privacy settings",
    "/sandbox": "Toggle sandbox mode",
    "/onboarding": "First-time setup guide",
    "/env": "View/set environment variables",
    "/voice": "Voice input (speak to chat)",
    "/ide": "IDE bridge (start/stop)",
    "/desktop": "Desktop app integration",
    "/mobile": "Mobile companion",
    # Remote
    "/remote-setup": "Manage remote SSH hosts",
    "/remote-env": "Remote environment setup",
    "/teleport": "Transfer session to another device",
    "/attach": "Attach to session",
    "/detach": "Detach session (background)",
    "/peers": "Peer connections",
    # Plugins
    "/plugin": "Plugin management menu",
    "/plugin browse": "Browse & install marketplace plugins",
    "/plugin list": "List installed plugins",
    "/plugin install": "Install a plugin",
    "/plugin marketplace": "Manage marketplace sources",
    # AI workflows
    "/bughunter": "AI bug detection",
    "/perf-issue": "AI performance analysis",
    "/advisor": "AI code review advisor",
    "/security-review": "AI security audit",
    "/send": "Send message directly",
    "/assistant": "Enter assistant mode",
    "/agents-platform": "Multi-agent coordination",
    # Session
    "/fork": "Fork current session",
    "/resume": "Resume a saved session",
    # Debug / internal
    "/debug-tool-call": "Toggle tool call debug",
    "/ctx_viz": "Context window visualization",
    "/heapdump": "Memory snapshot",
    "/mock-limits": "Toggle mock rate limits",
    "/break-cache": "Clear cache",
    "/claim-main": "Claim main process",
    "/ant-trace": "API trace viewer",
    "/rate-limit-options": "Rate limit handling options",
    "/reset-limits": "Reset rate limit counters",
    "/extra-usage": "Detailed usage statistics",
    "/pipe-status": "Pipe mode status",
    "/pipes": "Multi-step pipe chains",
    # Feedback
    "/good-claude": "Positive feedback",
    "/poor": "Negative feedback",
    "/thinkback": "Year in review",
    # Misc
    "/upgrade": "Upgrade ccb",
    "/release-notes": "Show release notes",
    "/version": "Show version",
    "/buddy": "Toggle buddy mode",
    "/exit": "Exit ccb",
}

# Flat list for tab completion word lookup (used as fallback)
SLASH_COMMANDS = list(SLASH_COMMAND_DESCRIPTIONS.keys())


class SlashCompleter(Completer):
    """Prompt-toolkit completer that renders /commands with descriptions.

    Output in the completion menu:
        /help        Show commands and shortcuts
        /clear       Clear conversation and screen (Ctrl+L)
        /compact     Compact conversation to free context
        ...

    Matches by prefix case-insensitively; only activates when input starts
    with '/', so regular typing isn't polluted by command suggestions.
    """

    def __init__(self, commands: dict[str, str]) -> None:
        self._commands = commands
        # Pad command names to a stable width so the description column lines up
        self._name_width = max((len(c) for c in commands), default=10) + 2

    def get_completions(self, document, complete_event):  # type: ignore[override]
        text = document.text_before_cursor
        # Only complete when the whole input is a /command prefix (no args yet).
        if not text.startswith("/") or " " in text:
            return
        query = text.lower()
        for cmd, desc in self._commands.items():
            if not cmd.lower().startswith(query):
                continue
            # display: the command name, padded, in bright text
            display = [("class:completion-cmd", cmd.ljust(self._name_width))]
            yield Completion(
                text=cmd,
                start_position=-len(text),
                display=display,
                display_meta=desc,
            )


class AtFileCompleter(Completer):
    """Prompt-toolkit completer for @file references.

    Triggers when the user types '@' and suggests files from the project
    directory (using git ls-files or directory listing). Supports path
    prefixes like @src/ and fuzzy matching.
    """

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd

    def get_completions(self, document, complete_event):  # type: ignore[override]
        text = document.text_before_cursor

        # Find the last @ that is preceded by whitespace or is at start
        at_idx = -1
        for i in range(len(text) - 1, -1, -1):
            if text[i] == '@' and (i == 0 or text[i - 1] in ' \t\n'):
                at_idx = i
                break

        if at_idx < 0:
            return

        # Extract the partial path after @
        partial = text[at_idx + 1:]

        # Don't interfere with email-like patterns
        if at_idx > 0 and text[at_idx - 1] not in ' \t\n':
            return

        from ccb.at_mentions import get_file_suggestions
        suggestions = get_file_suggestions(partial, self._cwd, max_results=15)

        start_pos = -(len(partial) + 1)  # include the @

        for s in suggestions:
            is_dir = s.endswith("/")
            display_meta = "dir" if is_dir else ""
            yield Completion(
                text="@" + s,
                start_position=start_pos,
                display=[("class:completion-file", s)],
                display_meta=display_meta,
            )


class MergedCompleter(Completer):
    """Combines SlashCompleter and AtFileCompleter."""

    def __init__(self, completers: list[Completer]) -> None:
        self._completers = completers

    def get_completions(self, document, complete_event):  # type: ignore[override]
        for completer in self._completers:
            yield from completer.get_completions(document, complete_event)


class REPLApp:
    """Full-screen interactive REPL."""

    def __init__(
        self,
        version: str,
        model: str,
        cwd: str,
        provider: Any,
        session: Any,
        registry: Any,
        system_prompt: str,
        mcp_manager: Any | None = None,
        state: dict[str, Any] | None = None,
        output_format: str = "rich",
    ) -> None:
        self.version = version
        self.model = model
        self.cwd = cwd
        self.provider = provider
        self.session = session
        self.registry = registry
        self.system_prompt = system_prompt
        self.mcp_manager = mcp_manager
        self.state: dict[str, Any] = state or {"vim_mode": False}
        self.output_format = output_format

        # Message buffer — list of (style, text) formatted-text tuples
        self._msg_lines: list[tuple[str, str]] = []
        self._is_loading = False
        self._is_busy = False  # prevent concurrent submissions
        self._stream_text = ""
        self._current_task: asyncio.Task | None = None  # track running task for Ctrl+C
        self._scroll_back = 0  # how many extra lines to scroll back (Page Up)
        self._permission_pending = False  # permission dialog active
        self._permission_event: asyncio.Event | None = None
        self._permission_approved = False
        # User-question prompt (ask_user_question tool)
        self._user_question_pending = False
        self._user_question_event: asyncio.Event | None = None
        self._user_question_answer: str = ""
        self._refresh_handle: asyncio.Task | None = None  # periodic UI refresh during loading
        self._spinner_verb: str = "Working"  # current tool verb for footer display
        # Pending image/file attachments (populated by /image, drag-drop, Ctrl+V)
        self._pending_images: list[dict[str, Any]] = []
        self._pending_files: list[dict[str, Any]] = []

        # Build UI
        self._build_layout()
        self._build_keybindings()
        self._build_style()

        self.app: Application[None] = Application(
            layout=self._layout,
            key_bindings=self._kb,
            style=self._style,
            full_screen=True,
            mouse_support=False,  # Off so terminal handles text selection + link clicks
        )

    # ── Output API (used by display.py) ─────────────────────────────────

    def append_output(self, text: str, style: str = "") -> None:
        """Append plain text to the message area."""
        self._msg_lines.append((style, text))
        self._scroll_back = 0  # snap to bottom on new output
        self._invalidate()

    def append_ansi(self, ansi: str) -> None:
        """Append raw ANSI text (from rich console capture).

        Falls back to stripping ANSI codes if parsing fails — better to show
        plain text than garbled fragments.
        """
        try:
            frags = list(PTK_ANSI(ansi))
            self._msg_lines.extend(frags)
        except Exception:
            # Strip ANSI sequences and show as plain text
            import re
            plain = re.sub(r"\x1b\[[0-9;]*[mGKHfJ]", "", ansi)
            self._msg_lines.append(("class:msg-info", plain))
        self._invalidate()

    def clear_messages(self) -> None:
        self._msg_lines.clear()
        self._invalidate()

    def set_streaming(self, text: str) -> None:
        """Update the streaming text at the bottom of messages."""
        self._stream_text = text
        self._invalidate()

    def finish_streaming(self) -> None:
        self._stream_text = ""
        self._invalidate()

    def set_loading(self, loading: bool) -> None:
        self._is_loading = loading
        self._invalidate()
        if loading:
            # Start periodic refresh so spinner/elapsed time update
            self._start_loading_refresh()
        else:
            self._stop_loading_refresh()

    def _start_loading_refresh(self) -> None:
        """Periodically invalidate the UI during loading for live spinner."""
        if hasattr(self, "_refresh_handle") and self._refresh_handle:
            return  # already running

        async def _tick() -> None:
            while self._is_loading or self._is_busy:
                await asyncio.sleep(0.2)
                self._invalidate()
            self._refresh_handle = None

        self._refresh_handle = asyncio.ensure_future(_tick())

    def _stop_loading_refresh(self) -> None:
        if hasattr(self, "_refresh_handle") and self._refresh_handle:
            self._refresh_handle.cancel()
            self._refresh_handle = None

    def _invalidate(self) -> None:
        try:
            self.app.invalidate()
        except Exception:
            pass

    async def ask_permission_async(self, tool_name: str, summary: str) -> bool:
        """Show a permission prompt inside the REPL.

        Sets _permission_pending flag and waits on an asyncio.Event.
        The y/n/a keybindings resolve the event.
        """
        import asyncio

        self.append_output(f"  Allow {tool_name}? (y)es / (n)o / (a)lways ", "class:footer-loading")
        self._permission_pending = True
        self._permission_event = asyncio.Event()
        self._permission_approved = False
        self._invalidate()

        try:
            await asyncio.wait_for(self._permission_event.wait(), timeout=120)
        except asyncio.TimeoutError:
            self._permission_approved = False
        finally:
            self._permission_pending = False
            self._invalidate()

        approved = self._permission_approved
        if approved:
            self.append_output("✓\n", "class:dim")
        else:
            self.append_output("✗ Denied\n", "class:msg-error")
        return approved

    async def ask_user_question_async(self, question: str, options: list[str] | None = None) -> str:
        """Show a question inside the REPL and wait for user input via the input buffer."""
        import asyncio

        self._user_question_pending = True
        self._user_question_event = asyncio.Event()
        self._user_question_answer = ""
        self._invalidate()

        try:
            await asyncio.wait_for(self._user_question_event.wait(), timeout=300)
        except asyncio.TimeoutError:
            self._user_question_answer = "(timed out)"
        finally:
            self._user_question_pending = False
            self._invalidate()

        answer = self._user_question_answer.strip()
        if options and answer.isdigit():
            idx = int(answer) - 1
            if 0 <= idx < len(options):
                answer = options[idx]
        return answer if answer else "(no response)"

    def ask_permission_sync(self, tool_name: str, summary: str) -> bool:
        """Synchronous wrapper — schedules async version on the running loop."""
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're already inside the event loop — create a future
            future = asyncio.ensure_future(
                self.ask_permission_async(tool_name, summary))
            # This won't work synchronously; caller must use async version
            raise RuntimeError("Use ask_permission_async in async context")
        return loop.run_until_complete(
            self.ask_permission_async(tool_name, summary))

    # ── Layout ──────────────────────────────────────────────────────────

    def _build_layout(self) -> None:
        # Status bar (2 lines: title + model/cwd)
        self._status_control = FormattedTextControl(self._get_status_fragments)

        # Message area — fills all remaining space; content is bottom-anchored
        # by padding with empty lines in _get_message_fragments.
        self._message_control = FormattedTextControl(
            self._get_message_fragments,
            focusable=False,
        )
        # Attach mouse handler for scroll-wheel + click-to-jump support.
        # prompt_toolkit's Window intercepts scroll events before key bindings,
        # so we must handle them at the UIControl level.
        _repl_self = self
        def _msg_mouse_handler(mouse_event: Any) -> object:
            from prompt_toolkit.mouse_events import MouseEventType
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                groups = _repl_self._group_by_line(_repl_self._msg_lines)
                _repl_self._scroll_back = min(
                    _repl_self._scroll_back + 3, max(0, len(groups) - 3))
                _repl_self._invalidate()
                return None  # consumed
            elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                _repl_self._scroll_back = max(0, _repl_self._scroll_back - 3)
                _repl_self._invalidate()
                return None  # consumed
            return NotImplemented
        self._message_control.mouse_handler = _msg_mouse_handler  # type: ignore[assignment]

        # Input buffer with tab-complete on slash commands + @ file references.
        # Merge plugin-contributed slash commands so they autocomplete too.
        merged_cmds = dict(SLASH_COMMAND_DESCRIPTIONS)
        try:
            from ccb.plugins import discover_plugin_slash_commands
            for slash, info in discover_plugin_slash_commands().items():
                if slash not in merged_cmds:
                    merged_cmds[slash] = f"[plugin] {info['description']}"
        except Exception:
            pass
        completer = MergedCompleter([
            SlashCompleter(merged_cmds),
            AtFileCompleter(self.cwd),
        ])
        self._input_buffer = Buffer(
            name="input",
            completer=completer,
            accept_handler=self._on_submit,
            multiline=False,
            complete_while_typing=True,
        )

        # Footer
        self._footer_control = FormattedTextControl(self._get_footer_fragments)

        # ── Windows ──

        message_window = Window(
            self._message_control,
            wrap_lines=True,
            height=D(weight=1),  # fill remaining space
        )

        input_window = Window(
            BufferControl(self._input_buffer),
            height=1,
            style="class:input-line",
        )

        # ── Clickable "Jump to bottom" pill ──
        # Separate Window so clicks are captured by its own mouse handler.
        _repl_ref = self

        def _pill_fragments() -> list[tuple[str, str]]:
            if _repl_ref._scroll_back < 5:
                return []
            term_w = shutil.get_terminal_size().columns
            hidden = _repl_ref._scroll_back
            label = f" ⬇ {hidden} more below · click or press End "
            pad = max(0, (term_w - len(label) - 4) // 2)
            return [
                ("", " " * pad),
                ("class:jump-to-bottom-border", "◂ "),
                ("class:jump-to-bottom", label),
                ("class:jump-to-bottom-border", " ▸"),
            ]

        pill_control = FormattedTextControl(_pill_fragments, focusable=False)

        def _pill_mouse_handler(mouse_event: Any) -> object:
            from prompt_toolkit.mouse_events import MouseEventType
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                if _repl_ref._scroll_back > 0:
                    _repl_ref._scroll_back = 0
                    _repl_ref._invalidate()
                    return None  # consumed
            return NotImplemented

        pill_control.mouse_handler = _pill_mouse_handler  # type: ignore[assignment]

        pill_window = ConditionalContainer(
            Window(pill_control, height=1, style="class:jump-to-bottom-bar"),
            filter=Condition(lambda: _repl_ref._scroll_back >= 5),
        )

        body = HSplit([
            # ── Status bar (fixed 2 lines) ──
            Window(self._status_control, height=2, style="class:status-bar"),
            Window(height=1, char="─", style="class:divider"),
            # ── Messages: fills remaining space, newest at bottom ──
            message_window,
            # ── Jump to bottom pill (only when scrolled up) ──
            pill_window,
            # ── Divider ──
            Window(height=1, char="─", style="class:divider"),
            # ── Input row ──
            VSplit([
                Window(width=2, content=FormattedTextControl(
                    lambda: [("class:prompt-char", "❯ ")]
                )),
                input_window,
            ], height=1),
            # ── Footer ──
            Window(self._footer_control, height=1, style="class:footer"),
        ])

        self._layout = Layout(
            FloatContainer(
                content=body,
                floats=[
                    # Completion menu (for slash commands)
                    Float(
                        content=CompletionsMenu(max_height=10, scroll_offset=1),
                        xcursor=True,
                        ycursor=True,
                    ),
                ],
            ),
            focused_element=self._input_buffer,
        )

    # ── Fragment generators ─────────────────────────────────────────────

    def _get_status_fragments(self) -> list[tuple[str, str]]:
        model_name = self.session.model if self.session else self.model
        try:
            from ccb.config import get_active_account
            acct = get_active_account()
            acct_name = acct.get("_name", "") if acct else ""
        except Exception:
            acct_name = ""
        parts: list[tuple[str, str]] = [
            ("class:status-icon", " ✦ "),
            ("class:status-title", "CCB"),
            ("class:status-dim", f" v{self.version}  "),
            ("class:status-model", f"{model_name}"),
        ]
        if acct_name:
            parts.append(("class:status-dim", f"  ({acct_name})"))

        # ── Second line: cwd + context % + cost ──
        from ccb.cost_tracker import (
            get_cost_state, format_cost, format_tokens, context_percentage,
        )
        from ccb.model_limits import get_context_limit
        cost = get_cost_state()
        ctx_used = self.session.last_input_tokens if self.session else 0
        ctx_limit = get_context_limit(model_name)
        ctx_pct = context_percentage(ctx_used, ctx_limit)

        line2 = f"   {self.cwd}"
        parts.append(("class:status-dim", "\n" + line2))

        # Context usage
        if ctx_used > 0:
            ctx_color = "class:status-ctx-warn" if ctx_pct >= 70 else "class:status-dim"
            parts.append(("class:status-dim", "  │ "))
            parts.append((ctx_color, f"ctx {ctx_pct}%"))
            parts.append(("class:status-dim", f" ({format_tokens(ctx_used)}/{format_tokens(ctx_limit)})"))

        # Cost
        if cost.total_cost_usd > 0:
            parts.append(("class:status-dim", "  │ "))
            parts.append(("class:status-dim", format_cost(cost.total_cost_usd)))

        return parts

    @staticmethod
    def _group_by_line(parts: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
        """Group fragments into display lines.

        Returns a list of lines, where each line is a list of (style, text)
        fragments that together form one visible line (no trailing \\n).
        Breaks only on real \\n characters — inline formatting (e.g. border +
        text + `code` + text) stays grouped as one line.

        Critical: len(result) == true visible line count, so bottom-anchoring
        padding and scroll slicing operate on correct line units.
        """
        lines: list[list[tuple[str, str]]] = [[]]
        for style, text in parts:
            if not text:
                continue
            segs = text.split("\n")
            for i, seg in enumerate(segs):
                if seg:
                    lines[-1].append((style, seg))
                if i < len(segs) - 1:
                    # real newline → start new line group
                    lines.append([])
        # Drop trailing empty group (from a final \n)
        if lines and not lines[-1]:
            lines.pop()
        return lines

    @staticmethod
    def _flatten_line_groups(
        groups: list[list[tuple[str, str]]],
    ) -> list[tuple[str, str]]:
        """Render line groups back to prompt_toolkit fragments with \\n between them."""
        out: list[tuple[str, str]] = []
        for i, grp in enumerate(groups):
            out.extend(grp)
            out.append(("", "\n"))
        return out

    def _get_message_fragments(self) -> list[tuple[str, str]]:
        """Return fragments for the message area.

        FormattedTextControl in a non-focused Window always renders from the
        top, so we manually compute a visible window of LINE GROUPS (not raw
        fragments — one line may span many fragments when inline formatting
        is present) and prepend empty-line padding for bottom-anchoring.

        _scroll_back controls how far back in history we are viewing.
        When _scroll_back == 0 (default), the newest messages are shown.
        """
        raw: list[tuple[str, str]] = list(self._msg_lines)
        if self._stream_text:
            # Streaming text with bold orange border, matching official Claude
            for sline in self._stream_text.split("\n"):
                raw.append(("class:msg-assistant-border", "  ┃ "))
                raw.append(("class:streaming", f"{sline}\n"))
        if self._is_loading and not self._stream_text:
            from ccb.cost_tracker import get_cost_state, format_duration
            cost = get_cost_state()
            elapsed = cost.elapsed_turn_ms
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            frame_idx = int(elapsed / 120) % len(frames)
            spinner = frames[frame_idx]
            elapsed_str = f" ({format_duration(elapsed)})" if elapsed > 1000 else ""
            raw.append(("class:loading", f"  {spinner} Thinking…{elapsed_str}\n"))
        if not raw:
            raw.append(("class:dim", "  Type a message to get started. /help for commands.\n"))

        # Group into DISPLAY LINES (one entry = one visible line worth of frags)
        line_groups = self._group_by_line(raw)
        total_lines = len(line_groups)

        # Estimate available height for the message area
        term_h = shutil.get_terminal_size().lines
        # status(2) + divider(1) + divider(1) + input(1) + footer(1) = 6 fixed lines
        avail = max(3, term_h - 6)

        if total_lines <= avail:
            # Everything fits → pad top so content hugs the bottom (near input)
            pad = avail - total_lines
            result: list[tuple[str, str]] = []
            if pad > 0:
                result.append(("", "\n" * pad))
            result.extend(self._flatten_line_groups(line_groups))
            return result

        # More content than viewport — slice a window of `avail` lines
        # scroll_back=0 → bottom of content; scroll_back>0 → further up
        end_idx = total_lines - self._scroll_back
        start_idx = end_idx - avail
        if start_idx < 0:
            start_idx = 0
            end_idx = min(avail, total_lines)
        if end_idx > total_lines:
            end_idx = total_lines
            start_idx = max(0, end_idx - avail)

        visible_groups = line_groups[start_idx:end_idx]
        visible_count = len(visible_groups)

        # Header for hidden-above indicator (takes 1 line of the avail budget)
        header_frags: list[tuple[str, str]] = []
        if start_idx > 0:
            header_frags = [
                ("class:dim", f"  ↑ {start_idx} more lines (Ctrl+↑ or mouse wheel)\n")
            ]
            # One of `avail` slots goes to header → drop one line from visible end
            if visible_count > avail - 1:
                visible_groups = visible_groups[: avail - 1]
                visible_count = len(visible_groups)

        # Pad top so the visible tail sits at the bottom of the message area
        header_lines = 1 if header_frags else 0
        pad = max(0, avail - visible_count - header_lines)

        result: list[tuple[str, str]] = []
        if pad > 0:
            result.append(("", "\n" * pad))
        result.extend(header_frags)
        result.extend(self._flatten_line_groups(visible_groups))
        return result

    def _get_footer_fragments(self) -> list[tuple[str, str]]:
        from ccb.cost_tracker import get_cost_state, format_duration
        cost = get_cost_state()

        parts: list[tuple[str, str]] = []
        if self._user_question_pending:
            parts.append(("class:footer-loading", " ❓ Type your answer and press Enter"))
            return parts

        if self._permission_pending:
            parts.append(("class:footer-loading", " ⚠ Press (y)es / (n)o / (a)lways"))
            return parts

        if self._is_loading or self._is_busy:
            # Show elapsed time + spinner verb during streaming
            elapsed = cost.elapsed_turn_ms
            elapsed_str = format_duration(elapsed) if elapsed > 1000 else ""
            if self._is_loading:
                if self._stream_text:
                    verb = "Responding"
                elif elapsed < 2000:
                    verb = "Thinking"
                else:
                    verb = self._spinner_verb
                parts.append(("class:footer-loading", f" ✦ {verb}…"))
            else:
                parts.append(("class:footer-loading", " ⟳ Running…"))
            if elapsed_str:
                parts.append(("class:footer-dim", f" {elapsed_str}"))
            parts.append(("class:footer-dim", " · "))
            parts.append(("class:footer-dim", "Ctrl+C cancel"))
            return parts

        # ── Idle state ──
        parts.append(("class:footer-hint", " ? "))
        parts.append(("class:footer-dim", "help"))
        parts.append(("class:footer-dim", " · "))
        parts.append(("class:footer-dim", "esc+enter newline"))
        parts.append(("class:footer-dim", " · "))
        parts.append(("class:footer-dim", "pgup/dn scroll"))
        parts.append(("class:footer-dim", " · "))
        parts.append(("class:footer-dim", "ctrl+Y copy"))

        # Permission mode indicator
        try:
            from ccb.config import get_permission_mode
            perm = get_permission_mode()
            if perm == "bypassPermissions":
                parts.append(("class:footer-dim", " · "))
                parts.append(("class:footer-loading", "auto"))
            elif perm == "plan":
                parts.append(("class:footer-dim", " · plan"))
        except Exception:
            pass

        effort = self.state.get("effort", "high")
        parts.append(("class:footer-dim", f" · effort:{effort}"))
        if self.state.get("vim_mode"):
            parts.append(("class:footer-dim", " · vim"))

        # Show pending attachment count
        n_img = len(self._pending_images)
        n_file = len(self._pending_files)
        if n_img or n_file:
            labels = []
            if n_img:
                labels.append(f"{n_img} img")
            if n_file:
                labels.append(f"{n_file} file")
            parts.append(("class:footer-dim", " · "))
            parts.append(("class:footer-loading", f"📎 {', '.join(labels)}"))
        return parts

    # ── Key bindings ────────────────────────────────────────────────────

    def _build_keybindings(self) -> None:
        kb = KeyBindings()

        @kb.add("escape", "enter")
        def _newline(event: Any) -> None:
            event.current_buffer.insert_text("\n")

        @kb.add("c-d")
        def _exit(event: Any) -> None:
            event.app.exit()

        @kb.add("c-c")
        def _cancel(event: Any) -> None:
            if self._is_busy or self._is_loading:
                # Cancel running task if any
                if self._current_task and not self._current_task.done():
                    self._current_task.cancel()
                    self._current_task = None
                self._is_loading = False
                self._is_busy = False
                self.finish_streaming()
                self.append_output("  Interrupted.\n", "class:dim")
            else:
                buf = event.app.current_buffer
                if buf.text:
                    buf.reset()
                else:
                    event.app.exit()

        @kb.add("c-l")
        def _clear_screen(event: Any) -> None:
            self.clear_messages()

        # Scroll message area — multiple bindings for terminal compat
        @kb.add("pageup", eager=True)
        @kb.add("s-up", eager=True)
        @kb.add("c-up", eager=True)
        @kb.add("<scroll-up>", eager=True)
        def _scroll_up(event: Any) -> None:
            groups = self._group_by_line(self._msg_lines)
            self._scroll_back = min(self._scroll_back + 10, max(0, len(groups) - 3))
            self._invalidate()

        @kb.add("pagedown", eager=True)
        @kb.add("s-down", eager=True)
        @kb.add("c-down", eager=True)
        @kb.add("<scroll-down>", eager=True)
        def _scroll_down(event: Any) -> None:
            self._scroll_back = max(0, self._scroll_back - 10)
            self._invalidate()

        # Jump straight to the newest message (bottom)
        @kb.add("end", eager=True)
        @kb.add("c-end", eager=True)
        def _jump_to_bottom(event: Any) -> None:
            if self._scroll_back != 0:
                self._scroll_back = 0
                self._invalidate()

        # Permission dialog keybindings (y/n/a)
        def _resolve_permission(approved: bool, always: bool = False) -> None:
            if not self._permission_pending or not self._permission_event:
                return
            from ccb.permissions import set_bypass_all
            if always:
                set_bypass_all(True)
            self._permission_approved = approved
            self._permission_event.set()

        @kb.add("y", eager=True)
        def _perm_yes(event: Any) -> None:
            if self._permission_pending:
                _resolve_permission(True)
            else:
                event.current_buffer.insert_text("y")

        @kb.add("n", eager=True)
        def _perm_no(event: Any) -> None:
            if self._permission_pending:
                _resolve_permission(False)
            else:
                event.current_buffer.insert_text("n")

        @kb.add("a", eager=True)
        def _perm_always(event: Any) -> None:
            if self._permission_pending:
                _resolve_permission(True, always=True)
            else:
                event.current_buffer.insert_text("a")

        # Ctrl+V: paste image from clipboard (macOS).
        # If the clipboard has an image, attach it; otherwise let prompt_toolkit
        # handle normal text paste.
        @kb.add("c-v", eager=True)
        def _paste_image(event: Any) -> None:
            if self._permission_pending:
                return
            asyncio.ensure_future(self._try_clipboard_image())

        # Ctrl+Y: copy last assistant message to system clipboard
        @kb.add("c-y", eager=True)
        def _copy_last(event: Any) -> None:
            self._copy_last_message()

        self._kb = kb

    async def _try_clipboard_image(self) -> None:
        """Check clipboard for an image and add to pending attachments."""
        try:
            from ccb.images import get_clipboard_image_macos
            img = await asyncio.get_event_loop().run_in_executor(
                None, get_clipboard_image_macos,
            )
            if img:
                self._pending_images.append(img.to_dict())
                n = len(self._pending_images)
                self.append_output(
                    f"  📎 Image pasted from clipboard ({img.filename})"
                    f" [{n} pending]\n",
                    "class:msg-info",
                )
            else:
                self.append_output(
                    "  No image in clipboard. Drag an image file or use"
                    " /image <path>\n",
                    "class:dim",
                )
        except Exception as e:
            self.append_output(f"  ✗ Clipboard error: {e}\n", "class:msg-error")

    def _copy_last_message(self) -> None:
        """Copy the last assistant message to the system clipboard."""
        from ccb.api.base import Role
        # Find last assistant message
        text = ""
        for msg in reversed(self.session.messages):
            if msg.role == Role.ASSISTANT and msg.content:
                text = msg.content
                break
        if not text:
            self.append_output("  No assistant message to copy.\n", "class:dim")
            return
        try:
            import subprocess
            proc = subprocess.run(
                ["pbcopy"], input=text.encode(), check=True,
                capture_output=True, timeout=3,
            )
            n_lines = text.count("\n") + 1
            self.append_output(
                f"  📋 Copied last response ({n_lines} lines) to clipboard.\n",
                "class:msg-info",
            )
        except FileNotFoundError:
            # Linux: try xclip / xsel
            for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
                try:
                    subprocess.run(cmd, input=text.encode(), check=True, capture_output=True, timeout=3)
                    n_lines = text.count("\n") + 1
                    self.append_output(
                        f"  📋 Copied last response ({n_lines} lines) to clipboard.\n",
                        "class:msg-info",
                    )
                    return
                except (FileNotFoundError, subprocess.CalledProcessError):
                    continue
            self.append_output("  ✗ No clipboard tool found (install pbcopy/xclip/xsel).\n", "class:msg-error")
        except Exception as e:
            self.append_output(f"  ✗ Copy failed: {e}\n", "class:msg-error")

    # ── Style ───────────────────────────────────────────────────────────

    def _build_style(self) -> None:
        self._style = Style.from_dict({
            # Status bar
            "status-bar": "bg:#1a1a2e",
            "status-icon": "bold #d77757",
            "status-title": "bold #d77757",
            "status-dim": "#666666",
            "status-model": "bold #ffffff",
            "status-ctx-warn": "bold #e6b800",  # yellow-orange for high context usage
            # Divider
            "divider": "#333333",
            # Input
            "input-line": "",
            "prompt-char": "bold #2196f3",
            # Footer
            "footer": "bg:#111111",
            "footer-hint": "bold #2196f3",
            "footer-dim": "#555555",
            "footer-loading": "#e6b800",
            # Messages — match official claude-code palette
            "streaming": "#d77757",                  # Claude orange (streaming text)
            "loading": "#888888 italic",
            "dim": "#666666",
            # User — blue label + bold left border
            "msg-user-label": "bold #2563eb",       # 🧑 You (official blue rgb(37,99,235))
            "msg-user-border": "bold #2563eb",      # ┃ thick blue left border
            "msg-user": "#e0e0e0",                   # user text
            # Assistant — orange label + bold left border
            "msg-assistant-label": "bold #d77757",   # 🤖 Claude (official orange rgb(215,119,87))
            "msg-assistant-border": "bold #d77757",  # ┃ thick orange left border
            "msg-border": "#555555",                 # │ dim border for tool results
            # Tools — ⏺ dot + bold name
            "msg-tool-dot": "#d77757",               # ⏺ orange dot
            "msg-tool-name": "bold #ffffff",         # tool name bold white
            "msg-tool-summary": "#888888",           # (summary) dim
            "msg-tool-output": "#888888",            # tool output
            # Markdown
            "md-heading": "bold #e0e0e0 underline",
            "md-bold": "bold #e0e0e0",
            "md-italic": "italic #cccccc",
            "md-code": "bold #56d4dd bg:#1a1a2e",     # inline code
            "md-code-block": "#56d4dd bg:#111111",     # code block
            "md-link": "underline #58a6ff",               # URLs (blue underline, clickable in terminal)
            # Misc
            "msg-error": "bold #f44336",
            "msg-info": "#888888",
            # Completion menu — Claude Code-style two-column popup
            "completion-menu": "bg:#1a1a1a",                                    # popup bg
            "completion-menu.completion": "bg:#1a1a1a #e0e0e0",                  # row default
            "completion-menu.completion.current": "bg:#d77757 #1a1a1a noinherit",  # selected: orange bg
            "completion-menu.meta.completion": "bg:#1a1a1a #888888",             # description (dim)
            "completion-menu.meta.completion.current": "bg:#d77757 #1a1a1a",     # selected description
            # Command-name column inside the completion display
            "completion-cmd": "bold #d77757",                                    # /command (orange)
            "completion-menu.completion.current completion-cmd": "bold #1a1a1a", # selected flips to dark text on orange bg
            # File-name column for @ file completions
            "completion-file": "#56d4dd",                                        # @file (cyan)
            "completion-menu.completion.current completion-file": "bold #1a1a1a", # selected: dark text
            # Scrollbar inside the completion menu
            "completion-menu.multi-column-meta": "bg:#1a1a1a #888888",
            "scrollbar.background": "bg:#222222",
            "scrollbar.button": "bg:#d77757",
            # Jump-to-bottom clickable pill (shown when scrolled up)
            "jump-to-bottom-bar": "bg:#111111",                  # pill row background
            "jump-to-bottom": "bold #1a1a2e bg:#d77757",         # orange pill with dark text
            "jump-to-bottom-border": "#d77757",                  # orange border/arrows
            # Parallel agent progress dashboard
            "agent-dash": "#56d4dd",                             # cyan status rows
        })

    # ── Input handling ──────────────────────────────────────────────────

    def _on_submit(self, buffer: Buffer) -> None:
        text = buffer.text.strip()
        if not text:
            return

        # If a user-question prompt is waiting, resolve it with the input
        if self._user_question_pending and self._user_question_event:
            self._user_question_answer = text
            self._user_question_event.set()
            return

        if self._is_busy:
            return  # prevent concurrent submissions

        # Set busy synchronously BEFORE dispatching async task
        self._is_busy = True
        self._invalidate()

        if text.startswith("/"):
            self._current_task = asyncio.ensure_future(self._handle_slash(text))
        else:
            self._current_task = asyncio.ensure_future(self._handle_user_message(text))

    async def _handle_slash(self, text: str) -> None:
        """Handle a slash command."""
        try:
            if text in ("/exit", "/quit", "/q"):
                self.app.exit()
                return

            if text == "/clear":
                self.clear_messages()
                return

            if text == "/help":
                self._show_help()
                return

            # Remember message count before command to detect /resume
            msg_count_before = len(self.session.messages)

            from ccb.commands import handle_command
            try:
                result = await handle_command(
                    text, self.session, self.provider, self.registry, self.cwd,
                    mcp_manager=self.mcp_manager, state=self.state,
                )
            except asyncio.CancelledError:
                self.append_output("  Cancelled.\n", "class:dim")
                return
            except Exception as e:
                # Write full traceback to debug log so nested-app / layout
                # issues don't get lost behind a single-line error.
                import traceback
                from pathlib import Path
                try:
                    log_path = Path.home() / ".claude" / "ccb-debug.log"
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with log_path.open("a") as f:
                        f.write(f"\n--- {text} @ {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                        traceback.print_exc(file=f)
                except Exception:
                    pass
                self.append_output(
                    f"  ✗ Command error: {e}\n"
                    f"  [dim]Full traceback: ~/.claude/ccb-debug.log[/dim]\n",
                    "class:msg-error",
                )
                return
        finally:
            self._is_busy = False
            self._is_loading = False
            self._invalidate()

        # Commands like /account may replace the provider
        if "_new_provider" in self.state:
            self.provider = self.state.pop("_new_provider")

        # If a resume-type command loaded messages, replay conversation history
        cmd_word = text.strip().split()[0].lower()
        if cmd_word in ("/resume", "/continue", "/sessions"):
            if len(self.session.messages) > 0 and len(self.session.messages) != msg_count_before:
                self._replay_session_history()

        # Update status bar model if it changed, AND regenerate the system
        # prompt so its "You are powered by the model X" line reflects the
        # newly-selected model. Without this, a fresh turn after /account or
        # /model would still carry the old model's identity in its system
        # prompt, causing the assistant to misidentify itself.
        if self.session.model and self.session.model != self.model:
            self.model = self.session.model
            try:
                from ccb.prompts import get_system_prompt
                self.system_prompt = get_system_prompt(self.cwd, model=self.model)
            except Exception:
                pass

        if result == "exit":
            self.app.exit()

    async def _handle_user_message(self, text: str) -> None:
        """Process user input: display, send to model, stream response.

        Detects image/file paths in the input (from drag-drop or manual
        typing), reads them, and attaches as multimodal content blocks.
        Also drains any pending attachments from /image or Ctrl+V paste.
        """
        # _is_busy already set by _on_submit
        from ccb.display import _apply_left_border
        from ccb.images import process_input_attachments
        from ccb.at_mentions import resolve_all_mentions

        # 1) Resolve @ file mentions (e.g. @README.md, @src/main.py#L1-50)
        text_after_at, at_files, at_images = resolve_all_mentions(text, self.cwd)

        # 2) Parse remaining input for image/file paths (drag-drop)
        remaining_text, auto_images, auto_files = process_input_attachments(text_after_at)

        # Merge all sources
        all_images = at_images + [img.to_dict() for img in auto_images]
        all_files = at_files + [fc.to_dict() for fc in auto_files]
        if self._pending_images:
            all_images.extend(self._pending_images)
            self._pending_images.clear()
        if self._pending_files:
            all_files.extend(self._pending_files)
            self._pending_files.clear()

        # Use remaining text (paths stripped) if auto-detected,
        # otherwise use the @-stripped text
        display_text = remaining_text or text_after_at or text

        # Show user message
        self._msg_lines.append(("", "\n"))
        self._msg_lines.append(("class:msg-user-label", "  🧑 You\n"))
        # Show @ mention indicators
        if at_files:
            names = ", ".join(f.get("filename", "file") for f in at_files)
            self._msg_lines.append(
                ("class:msg-info", f"  📄 @-mentioned: {names}\n")
            )
        # Show attachment indicators
        if all_images:
            names = ", ".join(
                img.get("filename", "image") for img in all_images
            )
            self._msg_lines.append(
                ("class:msg-info", f"  📎 {len(all_images)} image(s): {names}\n")
            )
        if all_files:
            names = ", ".join(
                fc.get("filename", "file") for fc in all_files
            )
            self._msg_lines.append(
                ("class:msg-info", f"  📄 {len(all_files)} file(s): {names}\n")
            )
        txt = display_text if display_text.endswith("\n") else display_text + "\n"
        bordered = _apply_left_border(
            [("class:msg-user", txt)], "class:msg-user-border"
        )
        self._msg_lines.extend(bordered)
        self._invalidate()

        self.session.add_user_message(
            display_text,
            images=all_images if all_images else None,
            files=all_files if all_files else None,
        )
        # Start cost turn timer so elapsed time shows from submission
        from ccb.cost_tracker import get_cost_state
        get_cost_state().start_turn()
        self._spinner_verb = "Working"  # reset verb for new turn
        self.set_loading(True)

        from ccb.loop import run_turn
        try:
            await run_turn(
                self.provider, self.session, self.registry, self.system_prompt,
                mcp_manager=self.mcp_manager,
                output_format=self.state.get("output_style", self.output_format),
                state=self.state,
            )
        except asyncio.CancelledError:
            self.append_output("  Cancelled.\n", "class:dim")
        except Exception as e:
            self.append_output(f"  ✗ Error: {e}\n", "class:msg-error")
        finally:
            self._is_busy = False
            self._is_loading = False
            self._stream_text = ""
            self._stop_loading_refresh()
            from ccb.cost_tracker import get_cost_state
            get_cost_state().end_turn()
            self._invalidate()
            self.session.save()

    def _replay_session_history(self) -> None:
        """Render all session messages in the REPL message area.

        Uses the same visual rules as live execution:
          - User messages: blue ┃ border + 🧑 You label
          - Assistant text: orange ┃ border + 🤖 Claude label (markdown)
          - Tool calls: ⏺ dot + name + (short input summary)
          - Tool results: │ prefix + collapsed 1-line summary
        """
        from ccb.api.base import Role
        from ccb.display import (
            _md_to_ptk, _apply_left_border, _summarize_tool_input,
            _summarize_tool_result,
        )
        self.clear_messages()
        count = len(self.session.messages)
        self._msg_lines.append(("class:dim", f"  ── Resumed session: {count} messages ──\n\n"))

        # Build tool_use_id → (name, input) map so tool results can produce
        # context-aware summaries (e.g. "Read 123 lines · ~/foo.py").
        tool_call_index: dict[str, tuple[str, dict[str, Any]]] = {}
        for msg in self.session.messages:
            if msg.role == Role.ASSISTANT:
                for tc in msg.tool_calls:
                    tool_call_index[tc.id] = (tc.name, tc.input or {})

        try:
            for msg in self.session.messages:
                if msg.role == Role.USER:
                    content = msg.content or ""
                    if content or msg.images or msg.files:
                        self._msg_lines.append(("", "\n"))
                        self._msg_lines.append(("class:msg-user-label", "  🧑 You\n"))
                        if msg.images:
                            names = ", ".join(
                                img.get("filename", "image") for img in msg.images
                            )
                            self._msg_lines.append(
                                ("class:msg-info", f"  📎 {len(msg.images)} image(s): {names}\n")
                            )
                        if msg.files:
                            names = ", ".join(
                                fc.get("filename", "file") for fc in msg.files
                            )
                            self._msg_lines.append(
                                ("class:msg-info", f"  📄 {len(msg.files)} file(s): {names}\n")
                            )
                        if content:
                            txt = content if content.endswith("\n") else content + "\n"
                            bordered = _apply_left_border(
                                [("class:msg-user", txt)], "class:msg-user-border"
                            )
                            self._msg_lines.extend(bordered)
                    if msg.tool_results:
                        for tr in msg.tool_results:
                            name, inp = tool_call_index.get(
                                tr.tool_use_id, ("?", {})
                            )
                            summary = _summarize_tool_result(
                                name, inp, tr.content or "", tr.is_error
                            )
                            style = (
                                "class:msg-error" if tr.is_error
                                else "class:msg-tool-output"
                            )
                            self._msg_lines.append(("class:msg-border", "  │ "))
                            self._msg_lines.append((style, f"{summary}\n"))
                elif msg.role == Role.ASSISTANT:
                    content = msg.content or ""
                    if content:
                        self._msg_lines.append(("", "\n"))
                        self._msg_lines.append(("class:msg-assistant-label", "  🤖 Claude\n"))
                        md_frags = _md_to_ptk(content)
                        bordered = _apply_left_border(md_frags, "class:msg-assistant-border")
                        self._msg_lines.extend(bordered)
                    for tc in msg.tool_calls:
                        name = tc.name or "?"
                        inp_summary = _summarize_tool_input(name, tc.input or {})
                        self._msg_lines.append(("class:msg-tool-dot", "  ⏺ "))
                        self._msg_lines.append(("class:msg-tool-name", f"{name}"))
                        if inp_summary:
                            self._msg_lines.append(
                                ("class:msg-tool-summary", f" ({inp_summary})")
                            )
                        self._msg_lines.append(("", "\n"))
        except Exception as e:
            self._msg_lines.append(("class:msg-error", f"  ✗ Replay error: {e}\n"))
        self._msg_lines.append(("", "\n"))
        self._scroll_back = 0  # snap to bottom to show latest
        self._invalidate()

    def _show_help(self) -> None:
        help_items = [
            ("  Commands:\n", "class:msg-assistant-label"),
            ("  /model          ", "class:dim"), ("Switch or view model\n", ""),
            ("  /account        ", "class:dim"), ("Switch account + model\n", ""),
            ("  /sessions       ", "class:dim"), ("List / resume sessions\n", ""),
            ("  /compact        ", "class:dim"), ("Compact conversation\n", ""),
            ("  /effort         ", "class:dim"), ("Set effort level\n", ""),
            ("  /permissions    ", "class:dim"), ("Permission mode\n", ""),
            ("  /diff           ", "class:dim"), ("Git diff\n", ""),
            ("  /doctor         ", "class:dim"), ("Run diagnostics\n", ""),
            ("  /status         ", "class:dim"), ("Show status\n", ""),
            ("  /clear          ", "class:dim"), ("Clear messages  (Ctrl+L)\n", ""),
            ("  /exit           ", "class:dim"), ("Exit            (Ctrl+D)\n", ""),
            ("\n", ""),
            ("  Shortcuts:\n", "class:dim"),
            ("  Enter       ", "class:dim"), ("Send message\n", ""),
            ("  Esc+Enter   ", "class:dim"), ("New line\n", ""),
            ("  Ctrl+C      ", "class:dim"), ("Cancel / Clear\n", ""),
            ("  Tab         ", "class:dim"), ("Complete /command\n", ""),
            ("\n", ""),
        ]
        for text, style in help_items:
            self._msg_lines.append((style, text))
        self._invalidate()

    # ── Run ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Start the full-screen REPL."""
        await self.app.run_async()


# ── Singleton reference (set by cli.py, read by display.py) ─────────

_active_repl: REPLApp | None = None


def get_active_repl() -> REPLApp | None:
    return _active_repl


def set_active_repl(repl: REPLApp | None) -> None:
    global _active_repl
    _active_repl = repl
