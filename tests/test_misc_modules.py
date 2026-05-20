"""Tests for remaining modules: settings_sync, skill_search, skills, installer, daemon, query_engine."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ccb.api.base import StreamEvent
from ccb.settings_sync import SettingsSync, SYNC_FILES
from ccb.skill_search import SkillSearchEngine, SkillMatch
from ccb.skills import Skill, SKILL_KIND, WORKFLOW_KIND, build_skill_prompt, find_skill, resolve_skill_prompt, search_skills
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

    def test_load_invalid_sync_config(self, tmp_path):
        cfg = tmp_path / "sync_config.json"
        cfg.write_text("not-json")
        s = SettingsSync()
        s._sync_file = cfg
        s._config = {"enabled": True}
        s._load_config()
        assert s._config == {}


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


class TestSkillsHelpers:
    def test_build_skill_prompt_with_text(self):
        skill = Skill(name="review", description="desc", prompt="Base prompt", source="bundled")
        prompt = build_skill_prompt(skill, "check latest diff")
        assert "Base prompt" in prompt
        assert "Additional context" in prompt
        assert "check latest diff" in prompt

    def test_build_skill_prompt_with_dict(self):
        skill = Skill(name="review", description="desc", prompt="Base prompt", source="bundled")
        prompt = build_skill_prompt(skill, {"focus": "security"})
        assert "Arguments:" in prompt
        assert '"focus": "security"' in prompt

    def test_find_skill_and_search_honor_kind(self):
        skills = [
            Skill(name="review", description="Review code", prompt="p1", source="bundled", kind=SKILL_KIND),
            Skill(name="shipit", description="Release workflow", prompt="p2", source="project", kind=WORKFLOW_KIND),
        ]
        with patch("ccb.skills.load_skills", return_value=skills):
            found = find_skill("/tmp", "shipit", kind=WORKFLOW_KIND)
            results = search_skills("/tmp", "release", kind=WORKFLOW_KIND)

        assert found is not None
        assert found.kind == WORKFLOW_KIND
        assert results[0].name == "shipit"

    def test_resolve_skill_prompt_returns_rendered_prompt(self):
        skills = [
            Skill(name="review", description="Review code", prompt="Base prompt", source="bundled", kind=SKILL_KIND),
        ]
        with patch("ccb.skills.load_skills", return_value=skills):
            resolved = resolve_skill_prompt("/tmp", "review", {"focus": "security"}, kind=SKILL_KIND)

        assert resolved is not None
        skill, prompt = resolved
        assert skill.name == "review"
        assert '"focus": "security"' in prompt


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


# ── Query Engine ──

class _FakeProvider:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        for chunk in self._chunks:
            if isinstance(chunk, StreamEvent):
                yield chunk
            else:
                yield SimpleNamespace(type="text", text=chunk)


class TestQueryEngine:
    @pytest.mark.asyncio
    async def test_run_query_uses_cwd_aware_system_prompt_and_provider_name(self):
        from ccb.query_engine import run_query

        provider = _FakeProvider(["hello", " world"])
        with (
            patch("ccb.config.load_global_config", return_value={"model": "cfg-model"}),
            patch("ccb.prompts.get_system_prompt", return_value="BASE") as get_system_prompt,
            patch("ccb.api.router.create_provider", return_value=provider) as create_provider,
        ):
            result = await run_query(
                "Say hi",
                provider_name="openai",
                output_format="json",
                cwd="/tmp/project",
            )

        assert result == "hello world"
        get_system_prompt.assert_called_once_with("/tmp/project", model="cfg-model")
        create_provider.assert_called_once_with(model="cfg-model", provider_type="openai")
        assert provider.calls[0]["system"].startswith("BASE")
        assert "Respond with valid JSON only." in provider.calls[0]["system"]

    @pytest.mark.asyncio
    async def test_run_query_streaming_uses_cwd_based_default_prompt(self):
        from ccb.query_engine import run_query_streaming

        provider = _FakeProvider(["a", "b"])
        with (
            patch("ccb.config.load_global_config", return_value={"model": "cfg-model"}),
            patch("ccb.prompts.get_system_prompt", return_value="BASE") as get_system_prompt,
            patch("ccb.api.router.create_provider", return_value=provider) as create_provider,
        ):
            chunks = [chunk async for chunk in run_query_streaming("Say hi", cwd="/tmp/project")]

        assert chunks == ["a", "b"]
        get_system_prompt.assert_called_once_with("/tmp/project", model="cfg-model")
        create_provider.assert_called_once_with(model="cfg-model", provider_type=None)
        assert provider.calls[0]["system"].startswith("BASE")
        assert "Be concise and direct." in provider.calls[0]["system"]

    @pytest.mark.asyncio
    async def test_run_query_uses_explicit_messages_when_provided(self):
        from ccb.api.base import Message, Role
        from ccb.query_engine import run_query

        provider = _FakeProvider(["ok"])
        prior_messages = [
            Message(role=Role.USER, content="first"),
            Message(role=Role.ASSISTANT, content="reply"),
            Message(role=Role.USER, content="second"),
        ]
        with (
            patch("ccb.config.load_global_config", return_value={"model": "cfg-model"}),
            patch("ccb.prompts.get_system_prompt", return_value="BASE"),
            patch("ccb.api.router.create_provider", return_value=provider),
        ):
            result = await run_query("ignored", cwd="/tmp/project", messages=prior_messages)

        assert result == "ok"
        assert provider.calls[0]["messages"] == prior_messages

    @pytest.mark.asyncio
    async def test_run_query_updates_session_usage(self):
        from ccb.query_engine import run_query
        from ccb.session import Session

        provider = _FakeProvider([
            "ok",
            StreamEvent(type="done", usage={"input_tokens": 123, "output_tokens": 45}),
        ])
        session = Session(id="s1")
        with (
            patch("ccb.config.load_global_config", return_value={"model": "cfg-model"}),
            patch("ccb.prompts.get_system_prompt", return_value="BASE"),
            patch("ccb.api.router.create_provider", return_value=provider),
        ):
            result = await run_query("hello", cwd="/tmp/project", session=session)

        assert result == "ok"
        assert session.total_input_tokens == 123
        assert session.total_output_tokens == 45
        assert session.last_input_tokens == 123
