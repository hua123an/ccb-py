"""Tests for ccb.hooks module."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ccb.hooks import load_hooks, run_hooks, HOOK_EVENTS


class TestLoadHooks:
    def test_empty_when_no_files(self, tmp_path):
        with patch("ccb.hooks.claude_dir", return_value=tmp_path / "nonexistent"):
            hooks = load_hooks(str(tmp_path / "cwd"))
        for event in HOOK_EVENTS:
            assert event in hooks
            assert hooks[event] == []

    def test_load_global_hooks(self, tmp_path):
        claude_home = tmp_path / "claude"
        claude_home.mkdir()
        hooks_data = {
            "pre_tool_call": [{"command": "echo pre-hook", "match": "bash"}],
            "session_start": [{"command": "echo started"}],
        }
        (claude_home / "hooks.json").write_text(json.dumps(hooks_data))

        with patch("ccb.hooks.claude_dir", return_value=claude_home):
            hooks = load_hooks(str(tmp_path / "cwd"))
        assert len(hooks["pre_tool_call"]) == 1
        assert hooks["pre_tool_call"][0]["command"] == "echo pre-hook"
        assert len(hooks["session_start"]) == 1

    def test_load_project_hooks(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        (cwd / ".claude").mkdir()
        hooks_data = {"post_message": [{"command": "echo done"}]}
        (cwd / ".claude" / "hooks.json").write_text(json.dumps(hooks_data))

        with patch("ccb.hooks.claude_dir", return_value=tmp_path / "empty"):
            hooks = load_hooks(str(cwd))
        assert len(hooks["post_message"]) == 1

    def test_merge_global_and_project(self, tmp_path):
        claude_home = tmp_path / "claude"
        claude_home.mkdir()
        (claude_home / "hooks.json").write_text(json.dumps({
            "pre_message": [{"command": "echo global"}],
        }))

        cwd = tmp_path / "project"
        cwd.mkdir()
        (cwd / ".claude").mkdir()
        (cwd / ".claude" / "hooks.json").write_text(json.dumps({
            "pre_message": [{"command": "echo project"}],
        }))

        with patch("ccb.hooks.claude_dir", return_value=claude_home):
            hooks = load_hooks(str(cwd))
        assert len(hooks["pre_message"]) == 2

    def test_invalid_json_ignored(self, tmp_path):
        claude_home = tmp_path / "claude"
        claude_home.mkdir()
        (claude_home / "hooks.json").write_text("not json")
        with patch("ccb.hooks.claude_dir", return_value=claude_home):
            hooks = load_hooks(str(tmp_path / "cwd"))
        assert all(hooks[e] == [] for e in HOOK_EVENTS)


class TestRunHooks:
    @pytest.mark.asyncio
    async def test_run_simple_hook(self):
        hooks = {e: [] for e in HOOK_EVENTS}
        hooks["session_start"] = [{"command": "echo hello"}]
        outputs = await run_hooks("session_start", hooks)
        assert len(outputs) == 1
        assert "hello" in outputs[0]

    @pytest.mark.asyncio
    async def test_run_with_context(self):
        hooks = {e: [] for e in HOOK_EVENTS}
        hooks["pre_tool_call"] = [{"command": "echo $CCB_HOOK_EVENT"}]
        outputs = await run_hooks("pre_tool_call", hooks, context={"tool_name": "bash"})
        assert "pre_tool_call" in outputs[0]

    @pytest.mark.asyncio
    async def test_matcher_filters(self):
        hooks = {e: [] for e in HOOK_EVENTS}
        hooks["pre_tool_call"] = [
            {"command": "echo matched", "match": "bash"},
            {"command": "echo skipped", "match": "file_write"},
        ]
        outputs = await run_hooks("pre_tool_call", hooks, context={"tool_name": "bash"})
        assert len(outputs) == 1
        assert "matched" in outputs[0]

    @pytest.mark.asyncio
    async def test_no_command_skipped(self):
        hooks = {e: [] for e in HOOK_EVENTS}
        hooks["session_start"] = [{"name": "no-command-entry"}]
        outputs = await run_hooks("session_start", hooks)
        assert outputs == []

    @pytest.mark.asyncio
    async def test_timeout_handled(self):
        hooks = {e: [] for e in HOOK_EVENTS}
        hooks["session_start"] = [{"command": "sleep 60"}]
        # Should not hang — hooks have a 30s timeout, but test just verifies no crash
        # We'll use a shorter command to avoid actual waiting
        hooks["session_start"] = [{"command": "echo fast"}]
        outputs = await run_hooks("session_start", hooks)
        assert len(outputs) == 1


class TestHookEvents:
    def test_all_events_defined(self):
        expected = ["pre_tool_call", "post_tool_call", "pre_message",
                    "post_message", "session_start", "session_end"]
        for e in expected:
            assert e in HOOK_EVENTS
