"""Full-screen pager overlay for displaying command output.

Follows the same nested-Application pattern as select_ui.py:
a new prompt_toolkit Application runs on top of the REPL,
and _restore_parent_repl() cleans up after exit.
"""
from __future__ import annotations

import asyncio
from typing import Sequence

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.styles import Style


def _build_keybindings(scroll_offset: list[int], line_count: int, page_size: int) -> KeyBindings:
    """Build key bindings for scrolling."""
    kb = KeyBindings()

    def _clamp():
        scroll_offset[0] = max(0, min(scroll_offset[0], max(0, line_count - page_size)))

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
        scroll_offset[0] -= page_size
        _clamp()
        event.app.invalidate()

    @kb.add("pagedown")
    @kb.add("f")
    @kb.add("space")
    def _pagedown(event):
        scroll_offset[0] += page_size
        _clamp()
        event.app.invalidate()

    @kb.add("g")
    def _top(event):
        scroll_offset[0] = 0
        event.app.invalidate()

    @kb.add("G", eager=True)
    def _bottom(event):
        scroll_offset[0] = max(0, line_count - page_size)
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
        return [
            ("class:title", f" {title} " if title else " "),
            ("class:footer", f" [{pos}/{total}  {pct}%]  q/Esc to close  ↑↓/jk scroll  g/G top/bottom "),
        ]

    title_control = FormattedTextControl(text=_get_title_bar)

    layout = Layout(
        HSplit([
            Window(height=1, content=title_control),
            Window(
                content=content_control,
                right_margins=[ScrollbarMargin()],
            ),
        ])
    )

    # We'll set page_size after we know the terminal height
    page_size = [40]  # default, will be updated

    kb = _build_keybindings(scroll_offset, line_count, page_size[0])

    app = Application(
        layout=layout,
        key_bindings=kb,
        style=PAGER_STYLE,
        full_screen=True,
        mouse_support=True,
    )

    # Update page_size when renderer knows the terminal height
    original_before_render = app.renderer.render

    def _patched_render(*a, **kw):
        h = app.renderer.output.get_size().rows
        if h and h > 2:
            page_size[0] = h - 2  # minus title and footer
        return original_before_render(*a, **kw)

    app.renderer.render = _patched_render

    try:
        await app.run_async()
    finally:
        # Restore parent REPL
        from ccb.select_ui import _restore_parent_repl
        await _restore_parent_repl()
