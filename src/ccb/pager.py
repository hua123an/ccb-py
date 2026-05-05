"""Full-screen pager overlay for displaying command output.

Follows the same nested-Application pattern as select_ui.py:
a new prompt_toolkit Application runs on top of the REPL,
and _restore_parent_repl() cleans up after exit.
"""
from __future__ import annotations

from typing import Sequence

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.styles import Style


def _build_keybindings(scroll_offset: list[int], line_count: int, page_size: list[int]) -> KeyBindings:
    """Build key bindings for scrolling."""
    kb = KeyBindings()

    def _clamp():
        scroll_offset[0] = max(0, min(scroll_offset[0], max(0, line_count - page_size[0])))

    @kb.add("q")
    @kb.add("escape")
    def _dismiss(event):
        event.app.exit()

    @kb.add("up")
    @kb.add("k")
    def _up(event):
        scroll_offset[0] -= 1
        _clamp()
        event.app.invalidate()

    @kb.add("down")
    @kb.add("j")
    def _down(event):
        scroll_offset[0] += 1
        _clamp()
        event.app.invalidate()

    @kb.add("pageup")
    @kb.add("b")
    def _pageup(event):
        scroll_offset[0] -= page_size[0]
        _clamp()
        event.app.invalidate()

    @kb.add("pagedown")
    @kb.add("f")
    @kb.add("space")
    def _pagedown(event):
        scroll_offset[0] += page_size[0]
        _clamp()
        event.app.invalidate()

    @kb.add("g")
    def _top(event):
        scroll_offset[0] = 0
        event.app.invalidate()

    @kb.add("G", eager=True)
    def _bottom(event):
        scroll_offset[0] = max(0, line_count - page_size[0])
        event.app.invalidate()

    @kb.add("c-c")
    def _ctrlc(event):
        event.app.exit()

    return kb


# ── Default pager style ──────────────────────────────────────────────
PAGER_STYLE = Style.from_dict({
    "title": "bold reverse",
    "footer": "reverse",
    "content": "",
    "scrollbar": "bg:#444444 #888888",
    "scrollbar.button": "bg:#888888 #ffffff",
    "pager-heading": "bold #d77757",
    "pager-cmd": "bold #d77757",
    "pager-dim": "#888888",
})


async def show_pager(
    content: Sequence[tuple[str, str]],
    title: str = "",
    _repl_app=None,
) -> None:
    """Display formatted content in a full-screen pager overlay.

    Args:
        content: List of (style, text) tuples, same format as _msg_lines.
                 Each tuple is one line. Style can be "" for default.
        title: Title shown at the top of the pager.
        _repl_app: The parent REPL Application (for restore).
    """
    if not content:
        return

    raw_lines: list[tuple[str, str]] = list(content)
    line_count = len(raw_lines)
    scroll_offset = [0]
    page_size = [40]  # updated dynamically from terminal height

    def _get_visible_lines() -> list[tuple[str, str]]:
        """Return the slice of lines visible at current scroll offset."""
        return raw_lines[scroll_offset[0]:]

    content_control = FormattedTextControl(
        text=_get_visible_lines,
        focusable=True,
    )

    def _get_title_bar():
        pos = scroll_offset[0] + 1
        total = line_count
        pct = int(scroll_offset[0] / max(1, line_count - 1) * 100) if line_count > 1 else 0
        left = f" {title} " if title else " "
        right = f" [{pos}/{total}  {pct}%]  q/Esc close  ↑↓/jk scroll  g/G top/bottom "
        # Pad to fill terminal width
        try:
            import shutil
            cols = shutil.get_terminal_size().columns
        except Exception:
            cols = 120
        pad = max(1, cols - len(left) - len(right))
        return [
            ("class:title", left),
            ("class:title", " " * pad),
            ("class:footer", right),
        ]

    title_control = FormattedTextControl(text=_get_title_bar)

    # Title bar: 1 row at top, full width
    title_window = Window(
        height=1,
        content=title_control,
        dont_extend_width=False,
    )

    # Content: fills remaining space, scrollable
    content_window = Window(
        content=content_control,
        dont_extend_width=False,
        wrap_lines=False,
        right_margins=[ScrollbarMargin()],
    )

    layout = Layout(
        HSplit([title_window, content_window])
    )

    kb = _build_keybindings(scroll_offset, line_count, page_size)

    app = Application(
        layout=layout,
        key_bindings=kb,
        style=PAGER_STYLE,
        full_screen=True,
        alternate_screen=True,
        mouse_support=True,
    )

    # Dynamically update page_size from actual terminal height
    original_render = app.renderer.render

    def _patched_render(*a, **kw):
        try:
            h = app.output.get_size().rows
        except Exception:
            h = 40
        if h and h > 2:
            page_size[0] = h - 2  # minus title bar and footer hint
        return original_render(*a, **kw)

    app.renderer.render = _patched_render

    # Suspend parent REPL renderer so it doesn't draw behind us
    from ccb.repl import get_active_repl
    _parent = get_active_repl()
    if _parent is not None:
        _parent._nested_app_active = True
        try:
            _parent.app.renderer.erase()
        except Exception:
            pass

    try:
        await app.run_async()
    finally:
        if _parent is not None:
            _parent._nested_app_active = False
        from ccb.select_ui import _restore_parent_repl
        await _restore_parent_repl()
