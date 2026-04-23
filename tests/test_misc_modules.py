"""Tests for remaining modules: settings_sync, skill_search, installer, daemon, query_engine."""
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ccb.settings_sync import SettingsSync, SYNC_FILES
from ccb.skill_search import SkillSearchEngine, SkillMatch
from ccb.installer import get_install_info, generate_bash_completion, generate_zsh_completion, generate_fish_completion
from ccb.daemon_proc import Daemon, is_running, daemon_status


# ── SettingsSync ──

class TestSettingsSync:
    def test_disabled_by_default(self, tmp_path):
        with patch.object(SettingsSync, "__init__", lambda self: None):
            s = SettingsSync()
            s._config_dir = tmp_path / "claude"
            s._sync_dir = tmp_path / "sync"
            s._sync_file = tmp_path / "sync_config.json"
            s._config = {}
            assert s.enabled is False

    def test_configure(self, tmp_path):
        with patch.object(SettingsSync, "__init__", lambda self: None):
            s = SettingsSync()
            s._config_dir = tmp_path / "claude"
            s._sync_dir = tmp_path / "sync"
            s._sync_file = tmp_path / "sync_config.json"
            s._config = {}
            s._config_dir.mkdir(parents=True)
            s.configure(target=str(tmp_path / "sync_target"))
            assert s.enabled is True
            assert s.sync_method == "directory"

    def test_push_pull(self, tmp_path):
        config_dir = tmp_path / "claude"
        config_dir.mkdir()
        (config_dir / "settings.json").write_text('{"theme": "dark"}')
        sync_target = tmp_path / "sync_target"

        with patch.object(SettingsSync, "__init__", lambda self: None):
            s = SettingsSync()
            s._config_dir = config_dir
            s._sync_dir = tmp_path / "sync"
            s._sync_file = tmp_path / "sync_config.json"
            s._config = {"enabled": True, "target": str(sync_target)}

            pushed = s.push()
            assert "settings.json" in pushed
            assert (sync_target / "settings.json").exists()

            # Modify local and pull from sync
            (config_dir / "settings.json").write_text('{"theme": "modified"}')
            pulled = s.pull()
            assert "settings.json" in pulled
            # Pull should have overwritten local file
            content = (config_dir / "settings.json").read_text()
            assert '"theme": "dark"' in content

    def test_diff(self, tmp_path):
        config_dir = tmp_path / "claude"
        config_dir.mkdir()
        (config_dir / "settings.json").write_text('{"a": 1}')
        sync_target = tmp_path / "sync"
        sync_target.mkdir()
        (sync_target / "settings.json").write_text('{"a": 2}')

        with patch.object(SettingsSync, "__init__", lambda self: None):
            s = SettingsSync()
            s._config_dir = config_dir
            s._sync_dir = sync_target
            s._sync_file = tmp_path / "sync_config.json"
            s._config = {"enabled": True, "target": str(sync_target)}

            diffs = s.diff()
            assert any(d["file"] == "settings.json" and d["status"] == "modified" for d in diffs)

    def test_status(self, tmp_path):
        with patch.object(SettingsSync, "__init__", lambda self: None):
            s = SettingsSync()
            s._config_dir = tmp_path
            s._sync_dir = tmp_path / "sync"
            s._sync_file = tmp_path / "sync_config.json"
            s._config = {"enabled": False}
            status = s.status()
            assert status["enabled"] is False
            assert status["files_tracked"] == len(SYNC_FILES)


# ── SkillSearch ──

class TestSkillSearch:
    def test_index(self):
        engine = SkillSearchEngine()
        # Manually add skills since the imports happen inside try/except
        engine._skills = [
            SkillMatch(name="review", source="bundled", description="Code review", tags=["quality"]),
            SkillMatch(name="test", source="bundled", description="Generate tests"),
        ]
        engine._indexed = True
        assert engine.count == 2

    def test_search(self):
        engine = SkillSearchEngine()
        engine._skills = [
            SkillMatch(name="review", source="bundled", description="Code review", tags=["quality"]),
            SkillMatch(name="test", source="bundled", description="Generate tests", tags=["testing"]),
        ]
        engine._indexed = True
        results = engine.search("review")
        assert len(results) >= 1
        assert results[0].name == "review"

    def test_search_no_match(self):
        engine = SkillSearchEngine()
        engine._skills = [
            SkillMatch(name="review", source="bundled", description="Code review"),
        ]
        engine._indexed = True
        results = engine.search("xyznonexistent")
        assert len(results) == 0

    def test_list_all(self):
        engine = SkillSearchEngine()
        engine._skills = [
            SkillMatch(name="a", source="bundled", description=""),
            SkillMatch(name="b", source="plugin", description=""),
        ]
        engine._indexed = True
        assert len(engine.list_all()) == 2
        assert len(engine.list_all(source="plugin")) == 1

    def test_count(self):
        engine = SkillSearchEngine()
        engine._skills = [SkillMatch(name="a", source="bundled", description="")]
        assert engine.count == 1


# ── Installer ──

class TestInstaller:
    def test_get_install_info(self):
        info = get_install_info()
        assert "python" in info
        assert "bin_name" in info
        assert info["bin_name"] == "ccb"

    def test_bash_completion(self):
        with patch("ccb.repl.SLASH_COMMAND_DESCRIPTIONS", {"/help": "Help", "/quit": "Quit"}):
            comp = generate_bash_completion()
            assert "ccb" in comp
            assert "_ccb_completions" in comp

    def test_zsh_completion(self):
        with patch("ccb.repl.SLASH_COMMAND_DESCRIPTIONS", {"/help": "Help"}):
            comp = generate_zsh_completion()
            assert "#compdef ccb" in comp

    def test_fish_completion(self):
        with patch("ccb.repl.SLASH_COMMAND_DESCRIPTIONS", {"/help": "Help"}):
            comp = generate_fish_completion()
            assert "complete -c ccb" in comp


# ── Daemon ──

class TestDaemon:
    def test_daemon_status_not_running(self, tmp_path):
        pid_path = tmp_path / "pid"
        with patch("ccb.daemon_proc._PID_FILE", pid_path):
            assert is_running() is None

    def test_daemon_status_stale_pid(self, tmp_path):
        pid_path = tmp_path / "pid"
        pid_path.write_text("99999999")  # Very unlikely to be a real PID
        with patch("ccb.daemon_proc._PID_FILE", pid_path):
            result = is_running()
            # Should be None (process doesn't exist) and PID file cleaned up
            assert result is None

    def test_daemon_add_task(self):
        d = Daemon()
        d.add_periodic_task("test", lambda: None, interval=60)
        assert len(d._tasks) == 1
        assert d._tasks[0]["name"] == "test"

    def test_daemon_status_dict(self, tmp_path):
        with patch("ccb.daemon_proc._PID_FILE", tmp_path / "pid"):
            with patch("ccb.daemon_proc._LOG_FILE", tmp_path / "log"):
                status = daemon_status()
                assert status["running"] is False
                assert status["pid"] is None
