"""Tests for ccb.vim_mode and ccb.keybinding modules."""
import json
from pathlib import Path

import pytest

from ccb.vim_mode import VimMode, parse_vim_command, VIM_COMMANDS, get_vim_mode
from ccb.keybinding import KeyBindingManager, KeyBinding, DEFAULT_BINDINGS


class TestVimMode:
    def test_initial_state(self):
        v = VimMode()
        assert not v.enabled
        assert v.mode == "NORMAL"

    def test_toggle(self):
        v = VimMode()
        assert v.toggle() is True
        assert v.enabled
        assert v.mode == "NORMAL"
        assert v.toggle() is False
        assert not v.enabled

    def test_mode_indicator(self):
        v = VimMode()
        assert v.mode_indicator == "[N]"
        v.mode = "INSERT"
        assert v.mode_indicator == "[I]"
        v.mode = "VISUAL"
        assert v.mode_indicator == "[V]"
        v.mode = "COMMAND"
        assert v.mode_indicator == "[:]"

    def test_mode_style(self):
        v = VimMode()
        assert "4a90d9" in v.mode_style  # NORMAL blue
        v.mode = "INSERT"
        assert "2ecc71" in v.mode_style  # INSERT green

    def test_mode_case_insensitive(self):
        v = VimMode()
        v.mode = "insert"
        assert v.mode == "INSERT"

    def test_editing_mode_vi(self):
        v = VimMode()
        v.enabled = True
        from prompt_toolkit.enums import EditingMode
        assert v.get_editing_mode() == EditingMode.VI

    def test_editing_mode_emacs(self):
        v = VimMode()
        v.enabled = False
        from prompt_toolkit.enums import EditingMode
        assert v.get_editing_mode() == EditingMode.EMACS

    def test_create_keybindings(self):
        v = VimMode()
        bindings = v.create_keybindings()
        assert bindings is not None


class TestVimCommands:
    def test_parse_w(self):
        action, arg = parse_vim_command(":w")
        assert action == "save"

    def test_parse_q(self):
        action, arg = parse_vim_command(":q")
        assert action == "quit"

    def test_parse_wq(self):
        action, arg = parse_vim_command(":wq")
        assert action == "save_quit"

    def test_parse_e_with_arg(self):
        action, arg = parse_vim_command(":e myfile.py")
        assert action == "edit_file"
        assert arg == "myfile.py"

    def test_parse_unknown(self):
        action, arg = parse_vim_command(":xyz")
        assert action == "unknown"

    def test_parse_shell(self):
        action, arg = parse_vim_command(":!")
        assert action == "shell"

    def test_all_commands_defined(self):
        expected = ["w", "q", "wq", "q!", "help", "set", "noh", "e", "sp", "vs", "!"]
        for cmd in expected:
            assert cmd in VIM_COMMANDS


class TestVimSingleton:
    def test_get_vim_mode(self):
        v = get_vim_mode()
        assert isinstance(v, VimMode)


class TestKeyBindingManager:
    def test_defaults_loaded(self):
        mgr = KeyBindingManager()
        bindings = mgr.list_bindings()
        assert len(bindings) >= len(DEFAULT_BINDINGS)

    def test_bind(self):
        mgr = KeyBindingManager()
        mgr.bind("ctrl+shift+k", "custom_action", description="Custom")
        assert mgr.get_action("ctrl+shift+k") == "custom_action"

    def test_unbind(self):
        mgr = KeyBindingManager()
        mgr.bind("ctrl+shift+t", "test")
        assert mgr.unbind("ctrl+shift+t") is True
        assert mgr.get_action("ctrl+shift+t") is None

    def test_unbind_nonexistent(self):
        mgr = KeyBindingManager()
        assert mgr.unbind("nonexistent_combo") is False

    def test_override(self):
        mgr = KeyBindingManager()
        mgr.bind("enter", "custom_submit")
        assert mgr.get_action("enter") == "custom_submit"

    def test_mode_filtering(self):
        mgr = KeyBindingManager()
        mgr.bind("ctrl+x", "cut", mode="normal")
        mgr.bind("ctrl+x", "special", mode="insert")
        assert mgr.get_action("ctrl+x", mode="normal") == "cut"
        assert mgr.get_action("ctrl+x", mode="insert") == "special"

    def test_conflict_detection(self):
        mgr = KeyBindingManager()
        mgr.bind("ctrl+q", "action1", mode="all")
        # Same keys + mode gets replaced, so no conflict
        conflicts = mgr.find_conflicts()
        # Conflicts occur only with duplicate keys+mode, which bind() prevents
        # by removing existing. Let's manually create one:
        mgr._bindings.append(KeyBinding(keys="ctrl+q", action="action2", mode="all"))
        conflicts = mgr.find_conflicts()
        assert len(conflicts) >= 1

    def test_reset_defaults(self):
        mgr = KeyBindingManager()
        mgr.bind("ctrl+z+z", "custom")
        mgr.reset_defaults()
        assert mgr.get_action("ctrl+z+z") is None

    def test_save_load(self, tmp_path):
        mgr = KeyBindingManager()
        mgr.bind("ctrl+shift+s", "super_save")
        path = tmp_path / "keybindings.json"
        mgr.save_user_config(path)
        assert path.exists()

        mgr2 = KeyBindingManager()
        count = mgr2.load_user_config(path)
        assert count > 0
        assert mgr2.get_action("ctrl+shift+s") == "super_save"

    def test_load_nonexistent(self):
        mgr = KeyBindingManager()
        count = mgr.load_user_config(Path("/nonexistent/path.json"))
        assert count == 0

    def test_load_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        mgr = KeyBindingManager()
        assert mgr.load_user_config(path) == 0

    def test_list_by_mode(self):
        mgr = KeyBindingManager()
        mgr.bind("ctrl+n", "normal_action", mode="normal")
        normal_bindings = mgr.list_bindings(mode="normal")
        # Should include "all" mode bindings + normal-specific
        assert any(b.action == "normal_action" for b in normal_bindings)
