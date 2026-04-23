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
