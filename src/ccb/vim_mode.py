"""Enhanced Vim mode for ccb-py REPL.

Provides a full Vim emulation layer on top of prompt_toolkit,
with Normal/Insert/Visual mode indicators and custom mappings.
"""
from __future__ import annotations

from typing import Any

from prompt_toolkit.enums import EditingMode
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.filters import vi_insert_mode, vi_navigation_mode


class VimMode:
    """Manages Vim mode state and keybindings."""

    def __init__(self) -> None:
        self.enabled = False
        self._mode = "NORMAL"  # NORMAL, INSERT, VISUAL, COMMAND

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        self._mode = value.upper()

    @property
    def mode_indicator(self) -> str:
        indicators = {
            "NORMAL": "[N]",
            "INSERT": "[I]",
            "VISUAL": "[V]",
            "COMMAND": "[:]",
        }
        return indicators.get(self._mode, "[?]")

    @property
    def mode_style(self) -> str:
        styles = {
            "NORMAL": "bg:#4a90d9 #ffffff bold",
            "INSERT": "bg:#2ecc71 #ffffff bold",
            "VISUAL": "bg:#e67e22 #ffffff bold",
            "COMMAND": "bg:#9b59b6 #ffffff bold",
        }
        return styles.get(self._mode, "")

    def toggle(self) -> bool:
        self.enabled = not self.enabled
        self._mode = "NORMAL" if self.enabled else "INSERT"
        return self.enabled

    def get_editing_mode(self) -> EditingMode:
        return EditingMode.VI if self.enabled else EditingMode.EMACS

    def create_keybindings(self) -> KeyBindings:
        """Create Vim-specific keybindings for the REPL."""
        bindings = KeyBindings()

        # jj to exit insert mode (common vim mapping)
        @bindings.add("j", "j", filter=vi_insert_mode)
        def _jj_escape(event: KeyPressEvent) -> None:
            event.app.vi_state.input_mode = event.app.vi_state.InputMode.NAVIGATION
            self._mode = "NORMAL"

        # ZZ to submit in normal mode
        @bindings.add("Z", "Z", filter=vi_navigation_mode)
        def _zz_submit(event: KeyPressEvent) -> None:
            buf = event.app.current_buffer
            buf.validate_and_handle()

        # gcc to toggle line comment (useful for code editing)
        @bindings.add("g", "c", "c", filter=vi_navigation_mode)
        def _gcc_comment(event: KeyPressEvent) -> None:
            buf = event.app.current_buffer
            line = buf.document.current_line
            if line.lstrip().startswith("# "):
                new_line = line.replace("# ", "", 1)
            else:
                new_line = "# " + line
            buf.document = buf.document.__class__(
                text=buf.text.replace(line, new_line, 1),
                cursor_position=buf.cursor_position,
            )

        # Ctrl-c in normal mode to cancel
        @bindings.add("c-c", filter=vi_navigation_mode)
        def _ctrl_c_cancel(event: KeyPressEvent) -> None:
            buf = event.app.current_buffer
            buf.reset()
            self._mode = "NORMAL"

        # dd to delete (clear) current line
        @bindings.add("d", "d", filter=vi_navigation_mode)
        def _dd_delete_line(event: KeyPressEvent) -> None:
            buf = event.app.current_buffer
            doc = buf.document
            lines = doc.text.splitlines(True)
            row = doc.cursor_position_row
            if 0 <= row < len(lines):
                lines.pop(row)
                new_text = "".join(lines)
                buf.text = new_text
                buf.cursor_position = min(buf.cursor_position, len(new_text))

        # yy to yank current line (copy to register)
        @bindings.add("y", "y", filter=vi_navigation_mode)
        def _yy_yank(event: KeyPressEvent) -> None:
            doc = event.app.current_buffer.document
            lines = doc.text.splitlines()
            row = doc.cursor_position_row
            if 0 <= row < len(lines):
                self._register = lines[row]

        # p to paste from register
        @bindings.add("p", filter=vi_navigation_mode)
        def _p_paste(event: KeyPressEvent) -> None:
            if hasattr(self, "_register") and self._register:
                buf = event.app.current_buffer
                buf.insert_text("\n" + self._register)

        # o to open new line below and enter insert
        @bindings.add("o", filter=vi_navigation_mode)
        def _o_open_below(event: KeyPressEvent) -> None:
            buf = event.app.current_buffer
            buf.insert_text("\n")
            event.app.vi_state.input_mode = event.app.vi_state.InputMode.INSERT
            self._mode = "INSERT"

        # O to open new line above and enter insert
        @bindings.add("O", filter=vi_navigation_mode)
        def _O_open_above(event: KeyPressEvent) -> None:
            buf = event.app.current_buffer
            doc = buf.document
            start = doc.text[:doc.cursor_position].rfind("\n")
            if start >= 0:
                buf.cursor_position = start
            else:
                buf.cursor_position = 0
            buf.insert_text("\n")
            buf.cursor_position -= 1
            event.app.vi_state.input_mode = event.app.vi_state.InputMode.INSERT
            self._mode = "INSERT"

        # A to go to end of line and enter insert
        @bindings.add("A", filter=vi_navigation_mode)
        def _A_append_eol(event: KeyPressEvent) -> None:
            buf = event.app.current_buffer
            doc = buf.document
            end = doc.text.find("\n", doc.cursor_position)
            if end >= 0:
                buf.cursor_position = end
            else:
                buf.cursor_position = len(doc.text)
            event.app.vi_state.input_mode = event.app.vi_state.InputMode.INSERT
            self._mode = "INSERT"

        # I to go to start of line and enter insert
        @bindings.add("I", filter=vi_navigation_mode)
        def _I_insert_bol(event: KeyPressEvent) -> None:
            buf = event.app.current_buffer
            doc = buf.document
            start = doc.text[:doc.cursor_position].rfind("\n")
            buf.cursor_position = start + 1 if start >= 0 else 0
            event.app.vi_state.input_mode = event.app.vi_state.InputMode.INSERT
            self._mode = "INSERT"

        # Mode tracking: detect when prompt_toolkit changes mode
        @bindings.add("escape", filter=vi_insert_mode)
        def _esc_to_normal(event: KeyPressEvent) -> None:
            self._mode = "NORMAL"

        @bindings.add("i", filter=vi_navigation_mode)
        def _i_to_insert(event: KeyPressEvent) -> None:
            event.app.vi_state.input_mode = event.app.vi_state.InputMode.INSERT
            self._mode = "INSERT"

        return bindings


# Vim command line parser
VIM_COMMANDS = {
    "w": "save",
    "q": "quit",
    "wq": "save_quit",
    "q!": "force_quit",
    "help": "help",
    "set": "settings",
    "noh": "no_highlight",
    "e": "edit_file",
    "sp": "split",
    "vs": "vsplit",
    "!": "shell",
}


def parse_vim_command(cmd: str) -> tuple[str, str]:
    """Parse a Vim-style command (e.g. ':wq', ':e file.py').
    Returns (action, argument).
    """
    cmd = cmd.lstrip(":")
    parts = cmd.split(maxsplit=1)
    command = parts[0] if parts else ""
    arg = parts[1] if len(parts) > 1 else ""
    action = VIM_COMMANDS.get(command, "unknown")
    return action, arg


# Module singleton
_vim: VimMode | None = None


def get_vim_mode() -> VimMode:
    global _vim
    if _vim is None:
        _vim = VimMode()
    return _vim
