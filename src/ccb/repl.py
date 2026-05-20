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
  │ [?] help [Enter] send …        │  footer
  └─────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import shutil
import time
from typing import Any

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI as PTK_ANSI
from prompt_toolkit.layout.processors import BeforeInput
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
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from ccb.session_repository import save_session
from ccb.session_runtime import emit_runtime_warning

INPUT_MAX_HEIGHT = 6
STATUS_MIN_HEIGHT = 2
STATUS_MAX_HEIGHT = 3
FOOTER_MIN_HEIGHT = 1
FOOTER_MAX_HEIGHT = 1


def accept_completion_or_submit(event: Any) -> None:
    """Accept the visible completion item, otherwise submit the input buffer."""
    buffer = event.current_buffer
    completion_state = buffer.complete_state
    if completion_state and completion_state.current_completion:
        buffer.apply_completion(completion_state.current_completion)
        return
    buffer.validate_and_handle()


def _strip_leading_hint_icon(text: str) -> str:
    stripped = text.lstrip()
    for prefix in ("💡", "Tip:", "TIP:"):
        if stripped.startswith(prefix):
            return stripped[len(prefix):].lstrip(" :-")
    return stripped


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
    "/flags": "Feature flags",
    "/daemon": "Manage background daemon",
    "/jobs": "Manage background jobs",
    "/acp": "ACP session status",
    "/stats": "Show detailed statistics",
    "/summary": "Summarize current conversation",
    "/history": "Show message history",
    # Sessions (see detailed below)
    "/sessions": "List saved sessions (current project)",
    "/continue": "Continue the last session",
    "/session": "Session management",
    "/rename": "Rename current session",
    "/tag": "Tag current session",
    "/share": "Share session output",
    # Git (detailed descriptions below)
    "/checkpoint": "Create a checkpoint",
    "/restore": "Restore from checkpoint",
    "/rewind": "Rewind conversation",
    # Memory / workspace
    "/memory": "Manage project memory",
    "/schedule": "Manage scheduled prompts",
    "/cron": "Alias for /schedule",
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
    # Git detailed
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

    @staticmethod
    def _sort_key(item: tuple[str, str]) -> tuple[int, str]:
        cmd, _desc = item
        return (len(cmd), cmd)

    def get_completions(self, document, complete_event):  # type: ignore[override]
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        query = text.lower()
        matches = sorted(
            (
                (cmd, desc)
                for cmd, desc in self._commands.items()
                if cmd.lower().startswith(query)
            ),
            key=self._sort_key,
        )
        for cmd, desc in matches:
            # display: the command name, padded, in bright text
            display = [("class:completion-cmd", cmd.ljust(self._name_width))]
            yield Completion(
                text=cmd,
                start_position=-len(text),
                display=display,
                display_meta=desc,
            )

    def first_match(self, text: str) -> str | None:
        matches = self.matching_commands(text, limit=1)
        return matches[0][0] if matches else None

    def matching_commands(self, text: str, *, limit: int | None = None) -> list[tuple[str, str]]:
        if not text.startswith("/"):
            return []
        query = text.lower()
        matches = sorted(
            (
                (cmd, desc)
                for cmd, desc in self._commands.items()
                if cmd.lower().startswith(query)
            ),
            key=self._sort_key,
        )
        return matches[:limit] if limit is not None else matches


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
        self._scroll_back = 0  # visual rows hidden below the current viewport
        self._stream_paused = False
        self._escape_pending_cancel: asyncio.Task | None = None
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
        self._nested_app_active: bool = False  # True while a nested overlay is running
        self._nested_prev_erase_when_done: bool | None = None
        self._scheduled_queue: asyncio.Queue[str] = asyncio.Queue()
        self._scheduled_scheduler: Any | None = None
        self._scheduled_drain_task: asyncio.Task | None = None
        self._slash_panel_limit = 6

        # Build UI
        self._build_layout()
        self._build_keybindings()
        self._build_style()

        self.app: Application[None] = Application(
            layout=self._layout,
            key_bindings=self._kb,
            style=self._style,
            full_screen=True,
            mouse_support=True,  # On so scroll-wheel events reach our message area handler
        )

    # ── Output API (used by display.py) ─────────────────────────────────

    def append_output(self, text: str, style: str = "") -> None:
        """Append plain text to the message area."""
        from ccb.display import repl_console, _safe_display_text
        text = _safe_display_text(text, max_line=400, max_total=8000)
        if repl_console._capturing:
            repl_console._capture_lines.append((style, text))
            return
        self._msg_lines.append((style, text))
        self._scroll_back = 0  # snap to bottom on new output
        self._invalidate()

    def _persist_session(self, action: str) -> None:
        try:
            save_session(self.session)
        except Exception as e:
            emit_runtime_warning(
                action,
                session_id=getattr(self.session, "id", ""),
                cwd=getattr(self.session, "cwd", "") or self.cwd,
                payload={"error": str(e)},
            )

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
        from ccb.display import _safe_display_text
        import time
        # Limit to last 2000 chars to prevent display overflow
        if len(text) > 2000:
            text = "…" + text[-1999:]
        text = _safe_display_text(text, max_line=400, max_total=2200)
        # Blinking cursor: toggle every 0.5s when streaming
        if not hasattr(self, "_last_stream_invalidate"):
            self._last_stream_invalidate = 0.0
        now = time.time()
        cursor = " " if (int(now * 2) % 2 == 0) else ""
        self._stream_text = text
        self._stream_cursor = cursor
        if self._stream_paused:
            return
        # Throttle invalidation to avoid flickering during streaming
        if now - self._last_stream_invalidate >= 0.08:  # ~12fps max for streaming
            self._last_stream_invalidate = now
            self._invalidate()

    def finish_streaming(self) -> None:
        self._stream_text = ""
        self._stream_paused = False
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
        if self._nested_app_active:
            return  # don't redraw while a nested overlay is running
        try:
            self.app.invalidate()
        except Exception:
            pass

    def _cancel_pending_escape(self) -> None:
        if self._escape_pending_cancel and not self._escape_pending_cancel.done():
            self._escape_pending_cancel.cancel()
        self._escape_pending_cancel = None

    def _cancel_current_turn(self, note: str = "  Interrupted.\n") -> None:
        self._cancel_pending_escape()
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        self._current_task = None
        self._is_loading = False
        self._is_busy = False
        self.finish_streaming()
        self.append_output(note, "class:dim")

    def _toggle_stream_pause(self) -> None:
        self._stream_paused = not self._stream_paused
        self._cancel_pending_escape()
        self._invalidate()

    def _queue_escape_cancel(self) -> None:
        self._cancel_pending_escape()

        async def _delayed_cancel() -> None:
            try:
                await asyncio.sleep(0.35)
            except asyncio.CancelledError:
                return
            self._escape_pending_cancel = None
            self._cancel_current_turn()

        self._escape_pending_cancel = asyncio.ensure_future(_delayed_cancel())

    def enter_nested_overlay(self) -> None:
        self._nested_app_active = True
        self._is_loading = False
        self._stop_loading_refresh()
        self._nested_prev_erase_when_done = getattr(self.app, "erase_when_done", None)
        try:
            self.app.erase_when_done = False
        except Exception:
            pass
        try:
            self.app.renderer.erase()
            self.app.output.erase_screen()
            self.app.output.cursor_goto(0, 0)
            self.app.output.flush()
        except Exception:
            pass

    def exit_nested_overlay(self) -> None:
        self._nested_app_active = False
        if self._nested_prev_erase_when_done is not None:
            try:
                self.app.erase_when_done = self._nested_prev_erase_when_done
            except Exception:
                pass
        self._nested_prev_erase_when_done = None

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

    async def ask_user_question_async(
        self,
        question: str,
        options: list[str] | list[dict[str, str]] | None = None,
    ) -> str:
        """Ask the user a question using an overlay when choices are available."""
        import asyncio

        if options:
            from ccb.select_ui import ask_text, select_one

            custom_label = "Other"
            normalized_options: list[dict[str, str]] = []
            for option in options:
                if isinstance(option, dict):
                    label = str(option.get("label") or option.get("value") or option.get("description") or "").strip()
                    if not label:
                        continue
                    normalized_options.append(
                        {
                            "label": label,
                            "value": str(option.get("value") or label).strip() or label,
                            "description": str(option.get("description") or "").strip(),
                        }
                    )
                else:
                    label = str(option).strip()
                    if label:
                        normalized_options.append(
                            {
                                "label": label,
                                "value": label,
                                "description": "",
                            }
                        )

            items = [
                {
                    "label": option["label"],
                    "description": option["description"],
                }
                for option in normalized_options
            ]
            items.append(
                {
                    "label": custom_label,
                    "description": "Type a custom answer",
                }
            )
            choice = await select_one(items, title=question)
            if choice is None:
                answer = "(user skipped)"
            elif 0 <= choice < len(normalized_options):
                answer = normalized_options[choice]["value"]
            elif choice == len(normalized_options):
                custom = await ask_text(
                    "Your answer",
                    placeholder="Type your own response",
                    title=question,
                )
                answer = custom.strip() if isinstance(custom, str) and custom.strip() else "(user skipped)"
            else:
                answer = "(user skipped)"
            self._msg_lines.append(("class:dim", f"  → {answer}\n"))
            self._invalidate()
            return answer

        self._msg_lines.append(("class:msg-info", f"\n  ❓ {question}\n"))

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

        # Show the user's choice in the message area
        display = answer if answer else "(no response)"
        self._msg_lines.append(("class:dim", f"  → {display}\n"))
        self._invalidate()

        return answer if answer else "(no response)"

    def ask_permission_sync(self, tool_name: str, summary: str) -> bool:
        """Synchronous wrapper — schedules async version on the running loop."""
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're already inside the event loop — create a future
            asyncio.ensure_future(
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
                _repl_self._scroll_by_visual_rows(_repl_self._wheel_scroll_step())
                _repl_self._invalidate()
                return None  # consumed
            elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                _repl_self._scroll_by_visual_rows(-_repl_self._wheel_scroll_step())
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
        try:
            from ccb.skills import build_skill_command_map, build_skill_invocation_map
            for slash, desc in build_skill_command_map(self.cwd).items():
                if slash not in merged_cmds:
                    merged_cmds[slash] = desc
            for slash, desc in build_skill_invocation_map(self.cwd).items():
                if slash not in merged_cmds:
                    merged_cmds[slash] = f"[invoke] {desc}"
        except Exception:
            pass
        completer = MergedCompleter([
            SlashCompleter(merged_cmds),
            AtFileCompleter(self.cwd),
        ])
        self._slash_completer = completer._completers[0] if completer._completers else None
        self._input_buffer = Buffer(
            name="input",
            completer=completer,
            accept_handler=self._on_submit,
            multiline=True,
            complete_while_typing=True,
        )

        # Footer / inline slash palette
        self._footer_control = FormattedTextControl(self._get_footer_fragments)
        self._slash_panel_control = FormattedTextControl(self._get_slash_panel_fragments)

        # ── Windows ──

        message_window = Window(
            self._message_control,
            wrap_lines=False,
            height=D(weight=1),  # fill remaining space
        )

        status_window = Window(
            self._status_control,
            height=D(min=STATUS_MIN_HEIGHT, max=STATUS_MAX_HEIGHT),
            wrap_lines=True,
            style="class:status-bar",
        )
        self._status_window = status_window

        input_window = Window(
            BufferControl(
                self._input_buffer,
                input_processors=[BeforeInput(self._input_prefix_fragments)],
            ),
            height=D(min=1, max=INPUT_MAX_HEIGHT),
            wrap_lines=True,
            style="class:input-line",
        )
        self._input_window = input_window

        # ── Clickable "Jump to bottom" pill ──
        # Separate Window so clicks are captured by its own mouse handler.
        _repl_ref = self

        def _pill_fragments() -> list[tuple[str, str]]:
            hidden = _repl_ref._hidden_below_visual_rows()
            if hidden <= 0:
                return []
            _, term_w = _repl_ref._get_screen_size()
            label = f" ⬇ {hidden} more below · click or press End "
            from prompt_toolkit.utils import get_cwidth
            pad = max(0, (term_w - get_cwidth(label) - 4) // 2)
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
            filter=Condition(lambda: _repl_ref._hidden_below_visual_rows() > 0),
        )

        input_row = VSplit([
            Window(width=3, content=FormattedTextControl(
                lambda: [("class:prompt-char", "› ")]
            )),
            input_window,
        ])
        self._input_row = input_row

        footer_window = Window(
            self._footer_control,
            height=D(min=FOOTER_MIN_HEIGHT, max=FOOTER_MAX_HEIGHT),
            wrap_lines=True,
            style="class:footer",
        )
        self._footer_window = footer_window

        slash_panel_window = ConditionalContainer(
            Window(
                self._slash_panel_control,
                height=D(min=1, max=self._slash_panel_limit + 2),
                wrap_lines=False,
                style="class:slash-panel",
            ),
            filter=Condition(lambda: self._should_show_slash_panel()),
        )

        body = HSplit([
            # ── Status bar (fixed 2 lines) ──
            status_window,
            Window(height=1, char="─", style="class:divider"),
            # ── Messages: fills remaining space, newest at bottom ──
            message_window,
            # ── Jump to bottom pill (only when scrolled up) ──
            pill_window,
            # ── Divider ──
            Window(height=1, char="─", style="class:divider"),
            # ── Persistent slash-command suggestions ──
            slash_panel_window,
            # ── Input row ──
            input_row,
            # ── Footer ──
            footer_window,
        ])

        self._layout = Layout(
            FloatContainer(
                content=body,
                floats=[
                    # Completion menu (for slash commands)
                    Float(
                        content=ConditionalContainer(
                            CompletionsMenu(max_height=10, scroll_offset=1),
                            filter=Condition(lambda: not self._should_show_slash_panel()),
                        ),
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
        _, cols = self._get_screen_size()
        try:
            from ccb.config import get_active_account
            acct = get_active_account()
            acct_name = acct.get("_name", "") if acct else ""
        except Exception:
            acct_name = ""
        shortened_model = self._shorten_display_text(model_name or "unknown", max(18, cols // 3))
        parts: list[tuple[str, str]] = [
            ("class:status-icon", " ● "),
            ("class:status-title", "CCB"),
            ("class:status-dim", f" {self.version}"),
            ("class:status-sep", " · "),
            ("class:status-model", shortened_model),
        ]
        if acct_name:
            parts.append(("class:status-sep", " · "))
            parts.append(("class:status-account", self._shorten_display_text(acct_name, max(12, cols // 6))))

        # ── Second line: cwd + context % + memory + cost ──
        from ccb.cost_tracker import (
            get_cost_state, format_cost, format_tokens, context_percentage,
        )
        from ccb.model_limits import get_context_limit
        from ccb.memory import get_store
        cost = get_cost_state()
        ctx_used = self.session.last_input_tokens if self.session else 0
        ctx_limit = get_context_limit(model_name)
        ctx_pct = context_percentage(ctx_used, ctx_limit)

        cwd_short = self._shorten_display_text(self.cwd, max(18, cols // 2))
        parts.append(("class:status-dim", "\n "))
        parts.append(("class:status-cwd", cwd_short))

        # Context usage with a compact meter
        if ctx_used > 0:
            if ctx_pct >= 85:
                ctx_color = "class:status-ctx-critical"
            elif ctx_pct >= 70:
                ctx_color = "class:status-ctx-warn"
            elif ctx_pct >= 50:
                ctx_color = "class:status-ctx-mid"
            else:
                ctx_color = "class:status-context"
            ctx_bar_len = 8
            filled = min(ctx_bar_len, max(0, round((ctx_pct / 100) * ctx_bar_len)))
            ctx_bar = "■" * filled + "·" * (ctx_bar_len - filled)
            parts.append(("class:status-sep", "  ·  "))
            parts.append(("class:status-dim", "ctx "))
            parts.append((ctx_color, ctx_bar))
            parts.append(("class:status-dim", f" {ctx_pct}%"))
            out_tokens = self.session.total_output_tokens if self.session else 0
            if out_tokens > 0:
                in_str = format_tokens(ctx_used)
                out_str = format_tokens(out_tokens)
                parts.append(("class:status-dim", f" ({in_str}/{out_str})"))

        mem_store = get_store()
        mem_count = mem_store.count
        if mem_count > 0:
            parts.append(("class:status-sep", "  ·  "))
            parts.append(("class:status-memory", f"mem {mem_count}"))

        if cost.total_cost_usd > 0:
            cost_str = format_cost(cost.total_cost_usd)
            parts.append(("class:status-sep", "  ·  "))
            parts.append(("class:status-cost", cost_str))

        return parts

    def _input_prefix_fragments(self) -> list[tuple[str, str]]:
        parts: list[tuple[str, str]] = [("class:prompt-char", "› ")]
        text = self._input_buffer.text
        cursor = self._input_buffer.cursor_position
        if cursor == len(text) and self._slash_completer and text.startswith("/"):
            match = self._slash_completer.first_match(text)
            if match and match != text:
                suffix = match[len(text):]
                if suffix:
                    parts.append(("class:input-ghost", suffix))
        return parts

    def _should_show_slash_panel(self) -> bool:
        text = self._input_buffer.text
        if self._input_buffer.cursor_position != len(text):
            return False
        if not self._slash_completer:
            return False
        return bool(self._slash_completer.matching_commands(text, limit=1))

    def _get_slash_panel_fragments(self) -> list[tuple[str, str]]:
        if not self._slash_completer:
            return []
        matches = self._slash_completer.matching_commands(
            self._input_buffer.text,
            limit=self._slash_panel_limit,
        )
        if not matches:
            return []

        _, cols = self._get_screen_size()
        inner_width = max(24, cols - 4)
        header = " Slash commands"
        footer = "Tab complete "
        body_width = max(0, inner_width - len(header) - len(footer))

        parts: list[tuple[str, str]] = [
            ("class:slash-panel-border", "  ╭"),
            ("class:slash-panel-border", "─" * inner_width),
            ("class:slash-panel-border", "╮\n"),
            ("class:slash-panel-border", "  │"),
            ("class:slash-panel-title", header),
            ("class:slash-panel-dim", " " * body_width),
            ("class:slash-panel-hint", footer),
            ("class:slash-panel-border", "│\n"),
        ]

        cmd_width = min(
            max((len(cmd) for cmd, _ in matches), default=8) + 2,
            max(12, inner_width // 2),
        )
        desc_width = max(0, inner_width - 3 - cmd_width)

        for idx, (cmd, desc) in enumerate(matches):
            is_primary = idx == 0
            marker_style = "class:slash-panel-selected" if is_primary else "class:slash-panel-dim"
            cmd_style = "class:slash-panel-selected" if is_primary else "class:slash-panel-command"
            desc_style = "class:slash-panel-selected-desc" if is_primary else "class:slash-panel-desc"
            trimmed_cmd = self._shorten_display_text(cmd, cmd_width)
            trimmed_desc = self._shorten_display_text(desc, desc_width) if desc_width > 0 else ""

            parts.append(("class:slash-panel-border", "  │"))
            parts.append((marker_style, " ❯ " if is_primary else "   "))
            parts.append((cmd_style, trimmed_cmd.ljust(cmd_width)))
            if desc_width > 0:
                parts.append((desc_style, trimmed_desc.ljust(desc_width)))
            parts.append(("class:slash-panel-border", "│\n"))

        parts.extend([
            ("class:slash-panel-border", "  ╰"),
            ("class:slash-panel-border", "─" * inner_width),
            ("class:slash-panel-border", "╯"),
        ])
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
                    lines.append([])

        while lines and not lines[0]:
            lines.pop(0)

        # A single trailing '\n' terminates the current line; it should not
        # become an extra blank display row. Only preserve additional empty
        # rows created by repeated trailing newlines.
        if lines and not lines[-1]:
            lines.pop()

        return lines or [[]]

    @staticmethod
    def _flatten_line_groups(
        groups: list[list[tuple[str, str]]],
    ) -> list[tuple[str, str]]:
        """Render line groups back to prompt_toolkit fragments with \\n between them."""
        out: list[tuple[str, str]] = []
        for i, grp in enumerate(groups):
            out.extend(grp)
            if i < len(groups) - 1:
                out.append(("", "\n"))
        return out

    @staticmethod
    def _continuation_prefix_for_group(
        group: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        if not group:
            return []
        style, text = group[0]
        if style == "class:msg-border":
            return [(style, text)]
        if style in {"class:msg-user-border", "class:msg-assistant-border"}:
            return [("", "    ")]
        if text.startswith("  ⏺ "):
            return [("", "    ")]
        if text.startswith("  "):
            return [("", "  ")]
        return []

    @classmethod
    def _wrap_line_group(
        cls,
        group: list[tuple[str, str]],
        cols: int,
    ) -> list[list[tuple[str, str]]]:
        if not group:
            return [[]]
        if cols <= 0:
            return [group]

        continuation = cls._continuation_prefix_for_group(group)
        continuation_width = sum(get_cwidth(text) for _, text in continuation)
        lines: list[list[tuple[str, str]]] = []
        current: list[tuple[str, str]] = []
        current_width = 0

        def flush() -> None:
            nonlocal current, current_width
            lines.append(current)
            current = list(continuation)
            current_width = continuation_width

        for style, text in group:
            if not text:
                continue
            for ch in text:
                ch_width = max(0, get_cwidth(ch))
                if current_width > 0 and ch_width > 0 and current_width + ch_width > cols:
                    flush()
                current.append((style, ch))
                current_width += ch_width

        if current or not lines:
            lines.append(current)
        return lines

    @classmethod
    def _wrap_line_groups(
        cls,
        groups: list[list[tuple[str, str]]],
        cols: int,
    ) -> list[list[tuple[str, str]]]:
        wrapped: list[list[tuple[str, str]]] = []
        for group in groups:
            wrapped.extend(cls._wrap_line_group(group, cols))
        return wrapped

    @staticmethod
    def _visual_rows_for_group(
        group: list[tuple[str, str]],
        cols: int,
    ) -> int:
        width = sum(get_cwidth(text) for _, text in group)
        if cols <= 0 or width <= 0:
            return 1
        return max(1, (width + cols - 1) // cols)

    @classmethod
    def _measure_fragment_rows(
        cls,
        parts: list[tuple[str, str]],
        cols: int,
        *,
        min_rows: int = 1,
        max_rows: int | None = None,
    ) -> int:
        groups = cls._group_by_line(parts)
        rows = sum(cls._visual_rows_for_group(group, cols) for group in groups)
        rows = max(min_rows, rows or 1)
        if max_rows is not None:
            rows = min(rows, max_rows)
        return rows

    def _measure_input_rows(self, cols: int) -> int:
        input_cols = max(1, cols - 2)
        rows = 0
        for line in self._input_buffer.text.split("\n"):
            width = get_cwidth(line)
            rows += max(1, (width + input_cols - 1) // input_cols) if width > 0 else 1
        return max(1, min(INPUT_MAX_HEIGHT, rows))

    @staticmethod
    def _shorten_display_text(text: str, max_width: int) -> str:
        if max_width <= 0 or get_cwidth(text) <= max_width:
            return text
        if max_width <= 1:
            return "…"

        head = max(4, (max_width - 1) // 2)
        tail = max(4, max_width - head - 1)
        shortened = f"{text[:head]}…{text[-tail:]}"
        while get_cwidth(shortened) > max_width and tail > 1:
            tail -= 1
            shortened = f"{text[:head]}…{text[-tail:]}"
        while get_cwidth(shortened) > max_width and head > 1:
            head -= 1
            shortened = f"{text[:head]}…{text[-tail:]}"
        return shortened

    def _get_screen_size(self) -> tuple[int, int]:
        try:
            size = self.app.output.get_size()
            return size.rows, size.columns
        except Exception:
            fallback = shutil.get_terminal_size()
            return fallback.lines, fallback.columns

    def _reserved_layout_rows(self, cols: int, *, pill_visible: bool | None = None) -> int:
        status_rows = self._measure_fragment_rows(
            self._get_status_fragments(),
            cols,
            min_rows=STATUS_MIN_HEIGHT,
            max_rows=STATUS_MAX_HEIGHT,
        )
        footer_rows = self._measure_fragment_rows(
            self._get_footer_fragments(),
            cols,
            min_rows=FOOTER_MIN_HEIGHT,
            max_rows=FOOTER_MAX_HEIGHT,
        )
        slash_rows = 0
        if self._should_show_slash_panel():
            slash_rows = self._measure_fragment_rows(
                self._get_slash_panel_fragments(),
                cols,
                min_rows=1,
                max_rows=self._slash_panel_limit + 2,
            )
        if pill_visible is None:
            pill_visible = self._scroll_back > 0
        pill_rows = 1 if pill_visible else 0
        divider_rows = 2
        return status_rows + divider_rows + pill_rows + slash_rows + self._measure_input_rows(cols) + footer_rows

    def _build_message_parts(self) -> list[tuple[str, str]]:
        raw: list[tuple[str, str]] = list(self._msg_lines)
        cursor_char = getattr(self, "_stream_cursor", "")
        if self._stream_text:
            stream_lines = self._stream_text.split("\n")
            if len(stream_lines) > 50:
                stream_lines = ["…"] + stream_lines[-49:]
            for sline in stream_lines[:-1]:
                raw.append(("class:msg-assistant-border", "    "))
                raw.append(("class:streaming", f"{sline}\n"))
            raw.append(("class:msg-assistant-border", "    "))
            raw.append(("class:streaming", f"{stream_lines[-1]}{cursor_char}\n"))
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
            raw.extend([
                ("class:empty-kicker", "  ccb-py\n"),
                ("class:empty-title", "  Ready for the next task\n"),
                ("class:empty-body", "  Ask for a code change, a review, a fix, or resume a previous session.\n"),
                ("class:empty-meta", "  Try /resume, /sessions, /help\n"),
            ])
        return raw

    def _message_view_state(self, *, pill_visible: bool | None = None) -> tuple[list[list[tuple[str, str]]], list[int], int, int]:
        term_h, cols = self._get_screen_size()
        logical_groups = self._group_by_line(self._build_message_parts())
        line_groups = self._wrap_line_groups(logical_groups, cols)
        vis_counts = [1 for _ in line_groups]
        avail = max(1, term_h - self._reserved_layout_rows(cols, pill_visible=pill_visible))
        return line_groups, vis_counts, sum(vis_counts), avail

    def _max_scroll_back(self) -> int:
        _, _, total_visual, avail = self._message_view_state(pill_visible=True)
        return max(0, total_visual - avail)

    def _scroll_by_visual_rows(self, delta: int) -> None:
        desired = self._scroll_back + delta
        max_scroll = self._max_scroll_back() if desired > 0 else 0
        self._scroll_back = max(0, min(desired, max_scroll))

    def _wheel_scroll_step(self) -> int:
        _, _, _, avail = self._message_view_state()
        return max(1, avail // 4)

    def _hidden_below_visual_rows(self) -> int:
        line_groups, vis_counts, _, avail = self._message_view_state()
        if not line_groups:
            return 0
        _, end_idx, _, _ = self._compute_visible_slice(vis_counts, avail)
        return sum(vis_counts[end_idx:])

    def _compute_visible_slice(
        self,
        vis_counts: list[int],
        avail: int,
    ) -> tuple[int, int, int, int]:
        total_lines = len(vis_counts)
        if total_lines == 0:
            return 0, 0, 0, 0

        max_scroll = max(0, sum(vis_counts) - avail)
        scroll_back = max(0, min(self._scroll_back, max_scroll))
        if scroll_back != self._scroll_back:
            self._scroll_back = scroll_back

        hidden_below = scroll_back
        end_idx = total_lines
        while end_idx > 0 and hidden_below >= vis_counts[end_idx - 1]:
            hidden_below -= vis_counts[end_idx - 1]
            end_idx -= 1
        if hidden_below > 0 and end_idx > 0:
            end_idx -= 1

        visual_used = 0
        start_idx = end_idx
        for i in range(end_idx - 1, -1, -1):
            vl = vis_counts[i]
            if visual_used + vl > avail:
                if start_idx == end_idx:
                    visual_used = avail
                    start_idx = i
                break
            visual_used += vl
            start_idx = i

        if scroll_back > 0 and start_idx == 0 and visual_used < avail:
            while end_idx < total_lines and visual_used < avail:
                vl = vis_counts[end_idx]
                if visual_used + vl > avail:
                    break
                visual_used += vl
                end_idx += 1

        return start_idx, end_idx, visual_used, scroll_back

    def _get_message_fragments(self) -> list[tuple[str, str]]:
        """Return fragments for the message area.

        FormattedTextControl in a non-focused Window always renders from the
        top, so we manually compute a visible window of LINE GROUPS (not raw
        fragments — one line may span many fragments when inline formatting
        is present) and prepend empty-line padding for bottom-anchoring.

        Visual line counts account for wrapping so that long lines don't push
        the newest messages off the bottom of the viewport.
        """
        line_groups, vis_counts, total_visual, avail = self._message_view_state()
        total_visual = sum(vis_counts)

        if total_visual <= avail:
            # Everything fits. Keep the live bottom view anchored near the
            # input line, but don't inject blank space while viewing history.
            pad = avail - total_visual if self._scroll_back == 0 else 0
            result: list[tuple[str, str]] = []
            if pad > 0:
                result.append(("", "\n" * pad))
            result.extend(self._flatten_line_groups(line_groups))
            return result

        start_idx, end_idx, visual_used, scroll_back = self._compute_visible_slice(vis_counts, avail)

        # Header for hidden-above indicator
        header_frags: list[tuple[str, str]] = []
        if start_idx > 0:
            hidden_above = sum(vis_counts[:start_idx])
            header_frags = [
                ("class:dim", f"  ↑ {hidden_above} more lines (Ctrl+↑ or mouse wheel)\n")
            ]
            # Recount: header costs 1 visual line
            visual_used = 1
            start_idx = end_idx
            for i in range(end_idx - 1, -1, -1):
                vl = vis_counts[i]
                if visual_used + vl > avail:
                    if start_idx == end_idx:
                        visual_used = avail
                        start_idx = i
                    break
                visual_used += vl
                start_idx = i

        visible_groups = line_groups[start_idx:end_idx]

        # Pad only at the live bottom. While scrolled into history, top-align
        # the slice so the user doesn't see a mostly empty viewport.
        pad = max(0, avail - visual_used) if scroll_back == 0 else 0
        output_result: list[tuple[str, str]] = []
        if pad > 0:
            output_result.append(("", "\n" * pad))
        output_result.extend(header_frags)
        output_result.extend(self._flatten_line_groups(visible_groups))
        return output_result

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
            if self._stream_paused:
                parts.append(("class:footer-loading", " Paused"))
                parts.append(("class:footer-dim", " · Esc resume · Ctrl+C cancel"))
                return parts
            if self._is_loading:
                if self._stream_text:
                    verb = "Responding"
                elif elapsed < 2000:
                    verb = "Thinking"
                else:
                    verb = self._spinner_verb
                parts.append(("class:footer-loading", f" {verb}…"))
            else:
                parts.append(("class:footer-loading", " Running…"))
            if elapsed_str:
                parts.append(("class:footer-dim", f" {elapsed_str}"))
            parts.append(("class:footer-dim", " · "))
            parts.append(("class:footer-dim", "Esc cancel · Esc Esc pause"))
            return parts

        # ── Idle state ──
        # Keep the idle footer to one compact action row.
        parts.append(("class:footer-hint", "Enter"))
        parts.append(("class:footer-dim", " send"))
        parts.append(("class:footer-sep", " · "))
        parts.append(("class:footer-hint", "Ctrl+C"))
        parts.append(("class:footer-dim", " cancel"))
        parts.append(("class:footer-sep", " · "))
        parts.append(("class:footer-hint", "PgUp/PgDn"))
        parts.append(("class:footer-dim", " scroll"))
        parts.append(("class:footer-sep", " · "))
        parts.append(("class:footer-hint", "Ctrl+Y"))
        parts.append(("class:footer-dim", " copy"))

        # Permission mode indicator
        try:
            from ccb.config import get_permission_mode
            perm = get_permission_mode()
            if perm == "bypassPermissions":
                parts.append(("class:footer-sep", " · "))
                parts.append(("class:footer-accent", "auto"))
            elif perm == "plan":
                parts.append(("class:footer-sep", " · "))
                parts.append(("class:footer-accent", "plan"))
        except Exception:
            pass

        # Show pending attachment count
        n_img = len(self._pending_images)
        n_file = len(self._pending_files)
        if n_img or n_file:
            labels = []
            if n_img:
                labels.append(f"{n_img} img")
            if n_file:
                labels.append(f"{n_file} file")
            parts.append(("class:footer-sep", " · "))
            parts.append(("class:footer-accent", f"attach {', '.join(labels)}"))
        effort = self.state.get("effort", "high")
        parts.append(("class:footer-sep", " · "))
        parts.append(("class:footer-accent", effort))
        if self.state.get("vim_mode"):
            parts.append(("class:footer-sep", " · "))
            parts.append(("class:footer-accent", "vim"))

        return parts

    # ── Key bindings ────────────────────────────────────────────────────

    def _build_keybindings(self) -> None:
        kb = KeyBindings()

        @kb.add("escape", "enter")
        def _newline(event: Any) -> None:
            event.current_buffer.insert_text("\n")

        @kb.add("escape", eager=True)
        def _escape(event: Any) -> None:
            if self._permission_pending or self._user_question_pending:
                return
            if self._stream_paused:
                self._toggle_stream_pause()
                return
            if self._is_busy or self._is_loading:
                if self._escape_pending_cancel and not self._escape_pending_cancel.done():
                    self._toggle_stream_pause()
                else:
                    self._queue_escape_cancel()
                return
            buf = event.current_buffer
            if buf.text:
                buf.reset()

        @kb.add("enter")
        def _submit(event: Any) -> None:
            accept_completion_or_submit(event)

        @kb.add("c-d")
        def _exit(event: Any) -> None:
            event.app.exit()

        @kb.add("c-c")
        def _cancel(event: Any) -> None:
            if self._is_busy or self._is_loading:
                self._cancel_current_turn()
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
        def _page_up(event: Any) -> None:
            _, _, _, avail = self._message_view_state()
            self._scroll_by_visual_rows(max(1, avail - 1))
            self._invalidate()

        @kb.add("pagedown", eager=True)
        @kb.add("s-down", eager=True)
        @kb.add("c-down", eager=True)
        def _page_down(event: Any) -> None:
            _, _, _, avail = self._message_view_state()
            self._scroll_by_visual_rows(-max(1, avail - 1))
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
            subprocess.run(
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
            # Global default — prevents transparency bleed-through
            "": "bg:#0f1117 #d7dce2",
            # Status bar
            "status-bar": "bg:#141824",
            "status-icon": "bold #7db8ff",
            "status-title": "bold #f2f4f8",
            "status-dim": "#7e8594",
            "status-sep": "#4b5563",
            "status-model": "bold #f2f4f8",
            "status-account": "#97a3b6",
            "status-cwd": "#8d96a6",
            "status-context": "#6bd58c",
            "status-ctx-mid": "#d8c46c",
            "status-ctx-warn": "#e8a266",
            "status-ctx-critical": "#ef7373",
            "status-memory": "#8bd3ff",
            "status-cost": "#9ba3b3",
            "status-suggest": "#8fd3ff",
            # Divider
            "divider": "#262b36 bg:#0f1117",
            # Input
            "input-line": "bg:#11141b",
            "prompt-char": "bold #76b6ff",
            "input-ghost": "#6a7280",
            "slash-panel": "bg:#0f1117",
            "slash-panel-border": "#2f3948",
            "slash-panel-title": "bold #e7ecf4",
            "slash-panel-hint": "#78b8ff",
            "slash-panel-dim": "#6b7482",
            "slash-panel-command": "#8fd3ff",
            "slash-panel-desc": "#8f98a8",
            "slash-panel-selected": "bold #eef4ff bg:#233755",
            "slash-panel-selected-desc": "#d7e6fb bg:#233755",
            # Footer
            "footer": "bg:#0b0d12",
            "footer-hint": "bold #72b7ff",
            "footer-dim": "#6f7785",
            "footer-sep": "#3c4453",
            "footer-loading": "#8fd3ff",
            "footer-accent": "#a6c8ff",
            # Empty state
            "empty-kicker": "#6f7785",
            "empty-title": "bold #eef2f7",
            "empty-body": "#9aa3b2",
            "empty-meta": "#7db8ff",
            # Messages — match official claude-code palette
            "streaming": "#8fd3ff",
            "loading": "#8b94a3 italic",
            "dim": "#7a8392",
            # User — blue label + bold left border
            "msg-user-label": "bold #7db8ff",
            "msg-user-border": "bold #4a82d8",
            "msg-user": "#dce3ec",
            # Assistant — orange label + bold left border
            "msg-assistant-label": "bold #9fd0ff",
            "msg-assistant-border": "bold #5f8fcb",
            "msg-border": "#505866",
            # Tools — ⏺ dot + bold name
            "msg-tool-dot": "#6faeff",
            "msg-tool-name": "bold #dbe6f3",
            "msg-tool-summary": "#8f98a8",
            "msg-tool-output": "#8f98a8",
            # Markdown
            "md-heading": "bold #eef2f7",
            "md-bold": "bold #eef2f7",
            "md-italic": "italic #c8d0db",
            "md-code": "bold #9fd0ff",
            "md-code-block": "#9fd0ff bg:#121722",
            "md-link": "underline #7db8ff",
            # Misc
            "msg-error": "bold #ef7373",
            "msg-info": "#8d96a6",
            # Completion menu — Claude Code-style two-column popup
            "completion-menu": "bg:#1a1a1a",                                    # popup bg
            "completion-menu.completion": "bg:#1a1a1a #e0e0e0",                  # row default
            "completion-menu.completion.current": "bg:#4c78b8 #eef4ff noinherit",
            "completion-menu.meta.completion": "bg:#1a1a1a #888888",             # description (dim)
            "completion-menu.meta.completion.current": "bg:#4c78b8 #dce8f8",
            # Command-name column inside the completion display
            "completion-cmd": "bold #8fd3ff",
            "completion-menu.completion.current completion-cmd": "bold #eef4ff",
            # File-name column for @ file completions
            "completion-file": "#56d4dd",                                        # @file (cyan)
            "completion-menu.completion.current completion-file": "bold #eef4ff",
            # Scrollbar inside the completion menu
            "completion-menu.multi-column-meta": "bg:#1a1a1a #888888",
            "scrollbar.background": "bg:#222222",
            "scrollbar.button": "bg:#4c78b8",
            # Jump-to-bottom clickable pill (shown when scrolled up)
            "jump-to-bottom-bar": "bg:#111111",                  # pill row background
            "jump-to-bottom": "bold #eef4ff bg:#4c78b8",
            "jump-to-bottom-border": "#4c78b8",
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

        if text.startswith("/"):
            self._is_busy = True
            self._current_task = asyncio.ensure_future(self._handle_slash(text))
        else:
            # Set busy synchronously BEFORE dispatching async task
            self._is_busy = True
            self._invalidate()
            self._current_task = asyncio.ensure_future(self._handle_user_message(text))

    # Commands whose output should open in the full-screen pager.
    # IMPORTANT: Only include commands that produce PURE TEXT output.
    # Commands that use interactive UI (select_one, ask_text, tools requiring
    # permission) must NOT be listed here, as capture mode would intercept
    # their interactive dialogs.
    _PAGER_COMMANDS = frozenset({
        "/help", "/diff", "/doctor", "/status", "/flags", "/peers",
        "/pipes", "/langfuse", "/sentry", "/acp",
        "/cost", "/usage", "/budget", "/stats", "/history", "/context",
        "/files", "/config", "/mcp", "/hooks", "/keybindings", "/events",
        "/jobs", "/daemon", "/schedule", "/cron", "/env", "/version",
        "/release-notes", "/extra-usage", "/pipe-status",
    })

    @staticmethod
    def _should_use_pager(text: str) -> bool:
        parts = text.strip().split()
        if not parts:
            return False
        cmd_word = parts[0].lower()
        if cmd_word in REPLApp._PAGER_COMMANDS:
            return True
        if cmd_word == "/plugin":
            sub = parts[1].lower() if len(parts) > 1 else ""
            rest = parts[2].lower() if len(parts) > 2 else ""
            return sub in ("list", "ls", "help", "-h", "--help", "?") or (
                sub in ("marketplace", "market") and rest in ("", "list", "ls")
            )
        return False

    async def _handle_slash(self, text: str) -> None:
        """Handle a slash command."""
        try:
            if text in ("/exit", "/quit", "/q"):
                self.app.exit()
                return

            if text == "/clear":
                self.clear_messages()
                return

            # /help: build content directly and show in pager
            if text == "/help":
                help_lines = self._build_help_lines()
                from ccb.pager import show_pager
                await show_pager(help_lines, title="/help", _repl_app=self.app)
                return

            # Remember message count before command to detect /resume
            msg_count_before = len(self.session.messages)

            # Determine if this command should use the pager. Interactive
            # commands must not be captured: their select/input UIs run as
            # independent full-screen nested applications.
            cmd_word = text.strip().split()[0].lower()
            use_pager = self._should_use_pager(text)

            from ccb.commands import handle_command
            from ccb.display import repl_console

            if use_pager:
                repl_console.start_capture()

            try:
                self._is_loading = False
                self._stop_loading_refresh()
                result = await handle_command(
                    text, self.session, self.provider, self.registry, self.cwd,
                    mcp_manager=self.mcp_manager, state=self.state,
                )
            except asyncio.CancelledError:
                if use_pager:
                    repl_console.stop_capture()
                self.append_output("  Cancelled.\n", "class:dim")
                return
            except Exception as e:
                if use_pager:
                    repl_console.stop_capture()
                # Write full traceback to debug log so nested-app / layout
                # issues don't get lost behind a single-line error.
                import traceback
                from pathlib import Path
                try:
                    log_path = Path.home() / ".ccb" / "ccb-debug.log"
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with log_path.open("a") as f:
                        f.write(f"\n--- {text} @ {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                        traceback.print_exc(file=f)
                except Exception:
                    pass
                self.append_output(
                    f"  ✗ Command error: {e}\n"
                    f"  [dim]Full traceback: ~/.ccb/ccb-debug.log[/dim]\n",
                    "class:msg-error",
                )
                return

            if use_pager:
                captured = repl_console.stop_capture()
                if captured:
                    from ccb.pager import show_pager
                    await show_pager(captured, title=cmd_word, _repl_app=self.app)

        finally:
            self._is_busy = False
            self._is_loading = False
            self._invalidate()

        # Commands like /account may replace the provider
        if "_new_provider" in self.state:
            self.provider = self.state.pop("_new_provider")

        # If a resume-type command loaded messages, replay conversation history
        if cmd_word in ("/resume", "/continue", "/sessions"):
            if len(self.session.messages) > 0 and len(self.session.messages) != msg_count_before:
                self._replay_session_history()
                self.append_output(
                    f"  Resumed session {self.session.id[:8]} ({len(self.session.messages)} msgs, model: {self.session.model})\n",
                    "class:msg-info",
                )

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
        remaining_text, auto_images, auto_files, auto_videos, auto_audios = process_input_attachments(text_after_at)

        # Merge all sources
        all_images = at_images + [img.to_dict() for img in auto_images]
        all_files = at_files + [fc.to_dict() for fc in auto_files]
        all_media = [v.to_dict() for v in auto_videos] + [a.to_dict() for a in auto_audios]
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
        from ccb.display import attachment_fragments
        self._msg_lines.extend(attachment_fragments(
            at_files=at_files,
            images=all_images,
            files=all_files,
            media=all_media,
        ))
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
            media=all_media if all_media else None,
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
            self._persist_session("repl_submit_persist_failed")

    async def _run_scheduled_prompt(self, text: str) -> None:
        self._msg_lines.append(("class:msg-info", f"\n  ⏰ Scheduled task fired: {text[:100]}\n"))
        self._invalidate()
        self._is_busy = True
        self._is_loading = True
        self.session.add_user_message(text)
        from ccb.loop import run_turn
        try:
            await run_turn(
                self.provider, self.session, self.registry, self.system_prompt,
                mcp_manager=self.mcp_manager,
                output_format=self.state.get("output_style", self.output_format),
                state={**self.state, "_scheduled_task": True},
            )
        except asyncio.CancelledError:
            self.append_output("  Scheduled task cancelled.\n", "class:dim")
        except Exception as e:
            self.append_output(f"  ✗ Scheduled task error: {e}\n", "class:msg-error")
        finally:
            self._is_busy = False
            self._is_loading = False
            self._stream_text = ""
            self._stop_loading_refresh()
            self._persist_session("repl_scheduled_persist_failed")
            self._invalidate()

    async def _drain_scheduled_queue(self) -> None:
        while True:
            prompt = await self._scheduled_queue.get()
            try:
                while self._is_busy or self._is_loading or self._permission_pending or self._user_question_pending:
                    await asyncio.sleep(0.25)
                await self._run_scheduled_prompt(prompt)
            finally:
                self._scheduled_queue.task_done()

    def _enqueue_scheduled_prompt(self, prompt: str) -> None:
        self._scheduled_queue.put_nowait(prompt)
        self._invalidate()

    async def _start_scheduled_tasks(self) -> None:
        from ccb.feature_flags import is_feature_enabled
        from ccb.cron_scheduler import CronScheduler

        if not is_feature_enabled("scheduled_tasks", True):
            return
        self._scheduled_scheduler = CronScheduler(
            project_dir=self.cwd,
            on_fire=self._enqueue_scheduled_prompt,
            is_loading=lambda: self._is_loading or self._is_busy,
            assistant_mode=bool(self.state.get("assistant_mode")),
            lock_owner=self.session.id,
        )
        self._scheduled_scheduler.start()
        self._scheduled_drain_task = asyncio.create_task(self._drain_scheduled_queue())

    async def _stop_scheduled_tasks(self) -> None:
        if self._scheduled_scheduler is not None:
            await self._scheduled_scheduler.stop()
            self._scheduled_scheduler = None
        if self._scheduled_drain_task is not None:
            self._scheduled_drain_task.cancel()
            try:
                await self._scheduled_drain_task
            except asyncio.CancelledError:
                pass
            self._scheduled_drain_task = None

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
            _summarize_tool_result, attachment_fragments, _safe_display_text,
        )
        from itertools import islice

        max_replay_messages = 80
        self.clear_messages()
        count = len(self.session.messages)
        self._msg_lines.append(("class:dim", f"  ── Resumed session: {count} messages ──\n\n"))
        if count > max_replay_messages:
            skipped = count - max_replay_messages
            self._msg_lines.append((
                "class:dim",
                f"  [showing last {max_replay_messages}; skipped {skipped} older messages]\n\n",
            ))
            replay_messages = list(islice(self.session.messages, skipped, None))
        else:
            replay_messages = self.session.messages

        # Build tool_use_id → (name, input) map so tool results can produce
        # context-aware summaries (e.g. "Read 123 lines · ~/foo.py").
        tool_call_index: dict[str, tuple[str, dict[str, Any]]] = {}
        for msg in replay_messages:
            if msg.role == Role.ASSISTANT:
                for tc in msg.tool_calls:
                    tool_call_index[tc.id] = (tc.name, tc.input or {})

        try:
            for msg in replay_messages:
                if msg.role == Role.USER:
                    content = msg.content or ""
                    if content or msg.images or msg.files or msg.media:
                        self._msg_lines.append(("", "\n"))
                        self._msg_lines.append(("class:msg-user-label", "  You\n"))
                        self._msg_lines.extend(attachment_fragments(
                            images=msg.images,
                            files=msg.files,
                            media=msg.media,
                        ))
                        if content:
                            txt = content if content.endswith("\n") else content + "\n"
                            bordered = _apply_left_border(
                                [("class:msg-user", txt)], "class:msg-user-border", border_char="    "
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
                            summary = _safe_display_text(summary, max_line=220, max_total=600)
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
                        self._msg_lines.append(("class:msg-assistant-label", "  Assistant\n"))
                        md_frags = _md_to_ptk(content)
                        bordered = _apply_left_border(md_frags, "class:msg-assistant-border", border_char="    ")
                        self._msg_lines.extend(bordered)
                    for tc in msg.tool_calls:
                        name = _safe_display_text(tc.name or "?", max_line=80, max_total=80)
                        inp_summary = _summarize_tool_input(tc.name or "?", tc.input or {})
                        inp_summary = _safe_display_text(inp_summary, max_line=160, max_total=300)
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

    @staticmethod
    def _build_help_lines() -> list[tuple[str, str]]:
        """Build help content as (style, text) tuples for the pager."""
        lines = [
            ("class:pager-heading", "  Commands\n"),
            ("class:pager-cmd", "  /model          "), ("", "Switch or view model\n"),
            ("class:pager-cmd", "  /account        "), ("", "Switch account + model\n"),
            ("class:pager-cmd", "  /sessions       "), ("", "List / resume sessions\n"),
            ("class:pager-cmd", "  /compact        "), ("", "Compact conversation\n"),
            ("class:pager-cmd", "  /effort         "), ("", "Set effort level\n"),
            ("class:pager-cmd", "  /permissions    "), ("", "Permission mode\n"),
            ("class:pager-cmd", "  /diff           "), ("", "Git diff\n"),
            ("class:pager-cmd", "  /doctor         "), ("", "Run diagnostics\n"),
            ("class:pager-cmd", "  /status         "), ("", "Show status\n"),
            ("class:pager-cmd", "  /flags          "), ("", "Feature flags\n"),
            ("class:pager-cmd", "  /peers          "), ("", "LAN peer discovery\n"),
            ("class:pager-cmd", "  /pipes          "), ("", "Named-pipe IPC\n"),
            ("class:pager-cmd", "  /langfuse       "), ("", "Langfuse observability\n"),
            ("class:pager-cmd", "  /sentry         "), ("", "Sentry error tracking\n"),
            ("class:pager-cmd", "  /acp            "), ("", "ACP protocol (Zed/Cursor)\n"),
            ("class:pager-cmd", "  /clear          "), ("", "Clear messages  (Ctrl+L)\n"),
            ("class:pager-cmd", "  /exit           "), ("", "Exit            (Ctrl+D)\n"),
            ("", "\n"),
            ("class:pager-heading", "  Shortcuts\n"),
            ("class:pager-dim", "  Enter       "), ("", "Send message\n"),
            ("class:pager-dim", "  Esc+Enter   "), ("", "New line\n"),
            ("class:pager-dim", "  Ctrl+C      "), ("", "Cancel / Clear\n"),
            ("class:pager-dim", "  Tab         "), ("", "Complete /command\n"),
            ("", "\n"),
        ]
        try:
            from ccb.skills import list_skills

            skills = list_skills(self.cwd)
            if skills:
                lines.extend([
                    ("class:pager-heading", "  Skills\n"),
                    ("class:pager-cmd", "  /skills         "), ("", "List or run skills\n"),
                    ("class:pager-cmd", "  /workflows      "), ("", "List or run workflows\n"),
                ])
                preview = skills[:3]
                for skill in preview:
                    lines.append(("class:pager-dim", f"  {skill.invocation_command:<16}"))
                    lines.append(("", f"{skill.description}\n"))
                if len(skills) > len(preview):
                    lines.append(("class:pager-dim", f"  … {len(skills) - len(preview)} more\n"))
                lines.append(("", "\n"))
        except Exception:
            pass
        return lines

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
        await self._start_scheduled_tasks()
        try:
            try:
                self.app.output.enter_alternate_screen()
                self.app.output.erase_screen()
                self.app.output.cursor_goto(0, 0)
                self.app.output.flush()
            except Exception:
                pass
            await self.app.run_async()
        finally:
            try:
                self.app.output.quit_alternate_screen()
                self.app.output.flush()
            except Exception:
                pass
            await self._stop_scheduled_tasks()


# ── Singleton reference (set by cli.py, read by display.py) ─────────

_active_repl: REPLApp | None = None


def get_active_repl() -> REPLApp | None:
    return _active_repl


def set_active_repl(repl: REPLApp | None) -> None:
    global _active_repl
    _active_repl = repl
