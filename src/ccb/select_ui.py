"""Interactive terminal selection UI using prompt_toolkit.

Provides ``select_one`` and ``ask_text`` which both work in:
- Full-screen REPL: runs as a nested Application overlaying the REPL
- Standalone / classic mode: runs as an inline Application
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth


async def _restore_parent_repl() -> None:
    """Aggressively restore parent REPL rendering after a nested Application exits.

    Called from both ``select_one`` and ``ask_text`` in their finally blocks.
    Sequence:
      1. renderer.erase() — wipe any residual output from the nested app
      2. renderer.reset() — clear internal cursor/screen-size state
      3. invalidate() — schedule a fresh render
      4. re-focus the input buffer so Enter works again
      5. brief sleep(0) yield to let the event loop process pending renders
    """
    from ccb.repl import get_active_repl
    repl = get_active_repl()
    if repl is None:
        return
    try:
        repl.app.renderer.erase()
    except Exception:
        pass
    try:
        repl.app.renderer.reset()
    except Exception:
        pass
    if not getattr(repl, "_is_busy", False):
        try:
            repl.app.invalidate()
        except Exception:
            pass
    try:
        repl.app.layout.focus(repl._input_buffer)
    except Exception:
        pass
    # Give the event loop a tick to actually process the render before any
    # subsequent nested Application is launched.
    try:
        await asyncio.sleep(0)
    except Exception:
        pass


# ── Shared style ────────────────────────────────────────────────────

SELECT_STYLE = Style.from_dict({
    "": "bg:#1a1a2e",
    "title": "bold #8fd3ff",
    "subtitle": "#888888",
    "selected-marker": "bold #2ecc71",
    "selected": "bold #ffffff",
    "selected-desc": "#4fc3f7",
    "selected-hint": "#888888",
    "selected-sep": "",
    "selected-match": "bold underline #4fc3f7",
    "label": "#cccccc",
    "desc": "#888888",
    "hint": "#666666",
    "match": "bold underline #4fc3f7",
    "search-label": "#888888",
    "search-value": "#ffffff",
    "search-placeholder": "#666666 italic",
    "empty": "#666666 italic",
    "border": "#444444",
    "bg": "bg:#1a1a2e",
})


async def select_one(
    items: list[dict[str, str]],
    title: str = "",
    active: int = 0,
    searchable: bool = False,
    search_placeholder: str = "Search",
    visible_count: int = 12,
    cancel_label: str = "cancel",
) -> int | None:
    """
    Interactive arrow-key selector.

    items: list of dicts with 'label' and optional 'description', 'hint'
    title: header text
    active: initially highlighted index

    Returns selected index, or None if cancelled (Esc / Ctrl-C).
    """
    if not items:
        return None

    def _clean(text: Any, max_width: int) -> str:
        s = "" if text is None else str(text)
        s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        s = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", s)
        s = "".join(ch if ord(ch) >= 32 else " " for ch in s)
        s = " ".join(s.split())
        if get_cwidth(s) > max_width:
            return _clip(s, max(1, max_width - 1)) + "…"
        return s

    # Determine terminal height for auto-sizing
    try:
        term_h = os.get_terminal_size().lines
        term_w = os.get_terminal_size().columns
        visible_count = min(visible_count, max(4, term_h - 8))
    except OSError:
        term_w = 80
        pass
    box_width = max(44, min(88, term_w - 6))
    inner_width = box_width - 2

    selected = [max(0, min(active, len(items) - 1))]
    query = [""]

    def _width(text: str) -> int:
        return get_cwidth(text)

    def _clip(text: str, max_width: int) -> str:
        if max_width <= 0:
            return ""
        out: list[str] = []
        used = 0
        for ch in text:
            ch_width = get_cwidth(ch)
            if used + ch_width > max_width:
                break
            out.append(ch)
            used += ch_width
        return "".join(out)

    def _pad(width: int) -> str:
        return " " * max(0, inner_width - width)

    def _match_text(text: str, term: str, base_style: str, match_style: str) -> list[tuple[str, str]]:
        if not term:
            return [(base_style, text)]
        lower_text = text.lower()
        lower_term = term.lower()
        start = lower_text.find(lower_term)
        if start < 0:
            return [(base_style, text)]
        end = start + len(term)
        parts: list[tuple[str, str]] = []
        if start > 0:
            parts.append((base_style, text[:start]))
        parts.append((match_style, text[start:end]))
        if end < len(text):
            parts.append((base_style, text[end:]))
        return parts

    def _filtered_indices() -> list[int]:
        if not searchable or not query[0]:
            return list(range(len(items)))
        needle = query[0].lower()
        return [
            i
            for i, item in enumerate(items)
            if needle in " ".join(
                [item.get("label", ""), item.get("description", ""), item.get("hint", "")]
            ).lower()
        ]

    def _visible_indices() -> tuple[list[int], int, int]:
        filtered = _filtered_indices()
        if not filtered:
            return [], 0, 0
        if selected[0] not in filtered:
            selected[0] = filtered[0]
        focus_pos = filtered.index(selected[0])
        height = max(1, visible_count)
        start = max(0, focus_pos - height // 2)
        end = min(len(filtered), start + height)
        start = max(0, end - height)
        return filtered, start, end

    def _get_fragments() -> list[tuple[str, str]]:
        parts: list[tuple[str, str]] = []

        # Top border
        parts.append(("class:border", "  ╭" + "─" * inner_width + "╮\n"))

        # Title row
        if title:
            clean_title = _clean(title, inner_width - 2)
            parts.append(("class:border", "  │ "))
            parts.append(("class:title", clean_title))
            pad = inner_width - _width(clean_title) - 1
            parts.append(("", " " * max(0, pad)))
            parts.append(("class:border", "│\n"))

            # Hint row
            if searchable:
                hint = f"↑↓ navigate · type to search · enter select · esc {cancel_label}"
            else:
                hint = f"↑↓ navigate · enter select · esc {cancel_label}"
            hint = _clean(hint, inner_width - 2)
            parts.append(("class:border", "  │ "))
            parts.append(("class:subtitle", hint))
            pad = inner_width - _width(hint) - 1
            parts.append(("", " " * max(0, pad)))
            parts.append(("class:border", "│\n"))

            parts.append(("class:border", "  ├" + "─" * inner_width + "┤\n"))

        # Search bar
        if searchable:
            parts.append(("class:border", "  │ "))
            clean_placeholder = _clean(search_placeholder, 18)
            parts.append(("class:search-label", f"🔎 {clean_placeholder}: "))
            if query[0]:
                clean_query = _clean(query[0], max(8, inner_width - _width(clean_placeholder) - 8))
                parts.append(("class:search-value", clean_query))
                pad = inner_width - _width(clean_placeholder) - _width(clean_query) - 6
            else:
                parts.append(("class:search-placeholder", "Type to filter..."))
                pad = inner_width - _width(clean_placeholder) - 20
            parts.append(("", " " * max(0, pad)))
            parts.append(("class:border", "│\n"))
            parts.append(("class:border", "  ├" + "─" * inner_width + "┤\n"))

        # Items
        filtered, start, end = _visible_indices()
        if not filtered:
            parts.append(("class:border", "  │ "))
            parts.append(("class:empty", "  No matches"))
            parts.append(("", " " * max(0, inner_width - 13)))
            parts.append(("class:border", "│\n"))
        else:
            if start > 0:
                parts.append(("class:border", "  │"))
                parts.append(("class:subtitle", "   ↑ more"))
                parts.append(("", " " * max(0, inner_width - 9)))
                parts.append(("class:border", "│\n"))

            for i in filtered[start:end]:
                item = items[i]
                label = _clean(item.get("label", ""), max(12, inner_width // 2))
                desc = _clean(item.get("description", ""), max(12, inner_width // 3))
                hint = _clean(item.get("hint", ""), max(8, inner_width // 4))

                parts.append(("class:border", "  │"))

                line_parts: list[tuple[str, str]] = []
                if i == selected[0]:
                    line_parts.append(("class:selected-marker", " ❯ "))
                    line_parts.extend(_match_text(label, query[0], "class:selected", "class:selected-match"))
                    if desc:
                        line_parts.append(("class:selected-sep", "  "))
                        line_parts.extend(_match_text(desc, query[0], "class:selected-desc", "class:selected-match"))
                    if hint:
                        line_parts.append(("class:selected-sep", "  "))
                        line_parts.extend(_match_text(hint, query[0], "class:selected-hint", "class:selected-match"))
                else:
                    line_parts.append(("", "   "))
                    line_parts.extend(_match_text(label, query[0], "class:label", "class:match"))
                    if desc:
                        line_parts.append(("", "  "))
                        line_parts.extend(_match_text(desc, query[0], "class:desc", "class:match"))
                    if hint:
                        line_parts.append(("", "  "))
                        line_parts.extend(_match_text(hint, query[0], "class:hint", "class:match"))

                # Calculate visible length for padding
                vis_len = sum(_width(t) for _, t in line_parts)
                if vis_len > inner_width:
                    remaining = inner_width
                    clipped: list[tuple[str, str]] = []
                    for style, chunk in line_parts:
                        if remaining <= 0:
                            break
                        piece = _clip(chunk, remaining)
                        clipped.append((style, piece))
                        remaining -= _width(piece)
                    line_parts = clipped
                    vis_len = sum(_width(t) for _, t in line_parts)
                for lp in line_parts:
                    parts.append(lp)
                parts.append(("", _pad(vis_len)))
                parts.append(("class:border", "│\n"))

            if end < len(filtered):
                parts.append(("class:border", "  │"))
                parts.append(("class:subtitle", "   ↓ more"))
                parts.append(("", " " * max(0, inner_width - 9)))
                parts.append(("class:border", "│\n"))

        # Bottom border
        parts.append(("class:border", "  ╰" + "─" * inner_width + "╯\n"))
        return parts

    def _move(delta: int) -> None:
        filtered = _filtered_indices()
        if not filtered:
            return
        if selected[0] not in filtered:
            selected[0] = filtered[0]
            return
        pos = filtered.index(selected[0])
        pos = max(0, min(len(filtered) - 1, pos + delta))
        selected[0] = filtered[pos]

    kb = KeyBindings()

    @kb.add("up")
    def _up(event: Any) -> None:
        _move(-1)

    @kb.add("down")
    def _down(event: Any) -> None:
        _move(1)

    if not searchable:
        @kb.add("k")
        def _vim_up(event: Any) -> None:
            _move(-1)

        @kb.add("j")
        def _vim_down(event: Any) -> None:
            _move(1)

    @kb.add("pageup")
    def _page_up(event: Any) -> None:
        _move(-max(1, visible_count - 1))

    @kb.add("pagedown")
    def _page_down(event: Any) -> None:
        _move(max(1, visible_count - 1))

    @kb.add("home")
    def _home(event: Any) -> None:
        filtered = _filtered_indices()
        if filtered:
            selected[0] = filtered[0]

    @kb.add("end")
    def _end(event: Any) -> None:
        filtered = _filtered_indices()
        if filtered:
            selected[0] = filtered[-1]

    @kb.add("enter")
    def _enter(event: Any) -> None:
        filtered = _filtered_indices()
        if not filtered:
            return
        if selected[0] not in filtered:
            selected[0] = filtered[0]
        event.app.exit(result=selected[0])

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event: Any) -> None:
        event.app.exit(result=None)

    if searchable:
        @kb.add("backspace")
        def _backspace(event: Any) -> None:
            if query[0]:
                query[0] = query[0][:-1]

        @kb.add("c-u")
        def _clear_query(event: Any) -> None:
            query[0] = ""

        @kb.add("<any>")
        def _type_search(event: Any) -> None:
            text = event.data
            if text and text.isprintable() and text not in ("\r", "\n") and len(query[0]) < 80:
                query[0] += text

    control = FormattedTextControl(_get_fragments)
    selector_window = Window(
        control,
        wrap_lines=False,
        style="class:bg",
        height=max(4, visible_count + (4 if title else 1) + (2 if searchable else 0)),
        dont_extend_height=True,
    )
    root = HSplit([
        Window(char=" ", style="class:bg", height=D(weight=1)),
        selector_window,
        Window(char=" ", style="class:bg", height=D(weight=1)),
    ], style="class:bg")

    # Suspend parent REPL renderer so it doesn't draw behind us
    from ccb.repl import get_active_repl
    _parent = get_active_repl()
    if _parent is not None:
        try:
            _parent.enter_nested_overlay()
        except Exception:
            pass

    selector_app: Application[int | None] = Application(
        layout=Layout(root),
        key_bindings=kb,
        style=SELECT_STYLE,
        full_screen=True,
        mouse_support=False,
    )
    try:
        selector_app.invalidate()
        await asyncio.sleep(0)
        return await selector_app.run_async()
    finally:
        try:
            selector_app.renderer.erase()
            selector_app.output.erase_screen()
            selector_app.output.cursor_goto(0, 0)
            selector_app.output.flush()
        except Exception:
            pass
        if _parent is not None:
            _parent.exit_nested_overlay()
        await _restore_parent_repl()


async def ask_text(
    label: str,
    *,
    placeholder: str = "",
    default: str = "",
    mask: bool = False,
    title: str = "",
) -> str | None:
    """Modal single-line text input. Returns the entered value, or None if cancelled.

    Works inside the full-screen REPL (nested Application) and in classic mode.
    Set ``mask=True`` for API keys — input is echoed as `*` characters.

    Uses the Frame widget (built-in border) + TextArea for robustness.
    """
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.widgets import Frame, Label, TextArea

    text_area = TextArea(
        multiline=False,
        password=mask,
        text=default,
        wrap_lines=False,
        height=1,
    )

    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event: Any) -> None:
        event.app.exit(result=text_area.text)

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event: Any) -> None:
        event.app.exit(result=None)

    # Build vertical children: optional title hint, label, text input, hint line
    body_children: list[Any] = []
    if placeholder and not default:
        body_children.append(Label(text=f"  e.g. {placeholder}", style="class:search-placeholder"))
    body_children.append(Label(text=f"  {label}", style="class:label"))
    body_children.append(text_area)
    body_children.append(Window(height=1))  # spacer
    body_children.append(Label(text="  enter confirm · esc cancel", style="class:subtitle"))

    frame = Frame(
        HSplit(body_children),
        title=title or "Input",
        style="class:border",
    )

    # Suspend parent REPL renderer so it doesn't draw behind us
    from ccb.repl import get_active_repl
    _parent = get_active_repl()
    if _parent is not None:
        try:
            _parent.enter_nested_overlay()
        except Exception:
            pass

    root = HSplit([
        Window(char=" ", style="class:bg", height=D(weight=1)),
        frame,
        Window(char=" ", style="class:bg", height=D(weight=1)),
    ], style="class:bg")

    app: Application[str | None] = Application(
        layout=Layout(root, focused_element=text_area),
        key_bindings=kb,
        style=SELECT_STYLE,
        full_screen=True,
        mouse_support=False,
    )
    try:
        return await app.run_async()
    finally:
        try:
            app.renderer.erase()
            app.output.erase_screen()
            app.output.cursor_goto(0, 0)
            app.output.flush()
        except Exception:
            pass
        if _parent is not None:
            _parent.exit_nested_overlay()
        await _restore_parent_repl()
