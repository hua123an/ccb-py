from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccb.api.base import Provider
from ccb.session import Session
from ccb.skills import Skill


class _Provider(Provider):
    async def stream(self, messages, tools, system="", max_tokens=16384, prefill="", temperature=None):
        if False:
            yield None

    def name(self) -> str:
        return "test"


@pytest.mark.asyncio
async def test_cmd_sessions_resume_uses_repository_helpers():
    from ccb.cmd_handlers import _session

    session = Session(id="active", cwd="/tmp/project", model="active-model")
    loaded = Session(id="saved", cwd="/tmp/project", model="saved-model")
    loaded.add_user_message("hello")
    provider = _Provider()

    with patch("ccb.cmd_handlers._session.load_session", return_value=loaded) as load_fn:
        handled = await _session.cmd_sessions("/resume", session, provider, "saved", {})

    assert handled is True
    load_fn.assert_called_once_with("saved")
    assert session.id == "saved"
    assert session.messages[0].content == "hello"


@pytest.mark.asyncio
async def test_cmd_fork_uses_repository_save():
    from ccb.cmd_handlers import _session

    session = Session(id="parent", cwd="/tmp/project", model="m")
    session.add_user_message("hello")

    with patch("ccb.cmd_handlers._session.save_session") as save_fn:
        handled = await _session.cmd_fork(session, "")

    assert handled is True
    save_fn.assert_called_once()
    forked = save_fn.call_args.args[0]
    assert forked.id != session.id
    assert [m.content for m in forked.messages] == ["hello"]


@pytest.mark.asyncio
async def test_cmd_fork_persist_failure_reports_error():
    from ccb.cmd_handlers import _session

    session = Session(id="parent", cwd="/tmp/project", model="m")
    session.add_user_message("hello")

    with (
        patch("ccb.cmd_handlers._session.save_session", side_effect=OSError("disk full")),
        patch("ccb.cmd_handlers._session.emit_runtime_warning") as warn_fn,
        patch("ccb.cmd_handlers._session.print_error") as print_error,
    ):
        handled = await _session.cmd_fork(session, "")

    assert handled is True
    warn_fn.assert_called_once()
    print_error.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_commit_uses_repository_save():
    from ccb.cmd_handlers import _git

    session = Session(id="s1", cwd="/tmp/project", model="m")

    with (
        patch("ccb.git_ops.git_available", return_value=True),
        patch("ccb.git_ops.diff_stat", side_effect=[SimpleNamespace(files_changed=1), SimpleNamespace(files_changed=1)]),
        patch("ccb.git_ops.generate_commit_message_prompt", return_value="commit prompt"),
        patch("ccb.loop.run_turn", AsyncMock()),
        patch("ccb.prompts.get_system_prompt", return_value="sys"),
        patch("ccb.cmd_handlers._git.save_session") as save_fn,
    ):
        handled = await _git.cmd_commit("", session, MagicMock(), MagicMock(), "/tmp/project")

    assert handled is True
    save_fn.assert_called_once_with(session)


@pytest.mark.asyncio
async def test_handle_command_plugin_prompt_uses_repository_save(tmp_path):
    from ccb.commands import handle_command

    cmd_file = tmp_path / "plugin.md"
    cmd_file.write_text("Do work")
    session = Session(id="s1", cwd=str(tmp_path), model="m")

    with (
        patch("ccb.plugins.discover_plugin_slash_commands", return_value={"/plugin-cmd": {"path": str(cmd_file)}}),
        patch("ccb.loop.run_turn", AsyncMock()),
        patch("ccb.prompts.get_system_prompt", return_value="sys"),
        patch("ccb.commands.save_session") as save_fn,
    ):
        handled = await handle_command("/plugin-cmd", session, MagicMock(), MagicMock(), str(tmp_path))

    assert handled is True
    save_fn.assert_called_once_with(session)


@pytest.mark.asyncio
async def test_handle_command_runs_custom_skill_prompt(tmp_path):
    from ccb.commands import handle_command

    session = Session(id="s1", cwd=str(tmp_path), model="m")
    skill = Skill(
        name="review",
        description="Review code",
        prompt="Review the project",
        source="bundled",
        kind="skill",
    )

    with (
        patch("ccb.skills.load_skills", return_value=[skill]),
        patch("ccb.loop.run_turn", AsyncMock()),
        patch("ccb.prompts.get_system_prompt", return_value="sys"),
        patch("ccb.commands.save_session") as save_fn,
    ):
        handled = await handle_command("/skills review focus on tests", session, MagicMock(), MagicMock(), str(tmp_path))

    assert handled is True
    assert session.messages[-1].content.startswith("Review the project")
    assert "focus on tests" in session.messages[-1].content
    save_fn.assert_called_once_with(session)


@pytest.mark.asyncio
async def test_cli_cleanup_persist_failure_emits_warning():
    from ccb.cli import _async_main

    provider = MagicMock()
    provider.set_model = MagicMock()
    fake_registry = MagicMock()
    fake_registry.names = []
    fake_registry._tools = {}

    with (
        patch("ccb.config.get_model", return_value="test-model"),
        patch("ccb.config.get_permission_mode", return_value="default"),
        patch("ccb.config.get_api_key", return_value="token"),
        patch("ccb.config.get_api_key_hint", return_value=None),
        patch("ccb.api.router.create_provider", return_value=provider),
        patch("ccb.tools.base.create_default_registry", return_value=fake_registry),
        patch("ccb.prompts.get_system_prompt", return_value="sys"),
        patch("ccb.loop.run_turn", AsyncMock()),
        patch("ccb.session_repository.save_session", side_effect=OSError("disk full")),
        patch("ccb.session_runtime.emit_runtime_warning") as warn_fn,
        patch("ccb.memory.get_extractor") as extractor_fn,
    ):
        extractor = extractor_fn.return_value
        extractor.set_provider.return_value = None
        extractor.extract_from_session = AsyncMock()
        extractor.extract_from_message = AsyncMock()

        await _async_main(
            initial_prompt="hello",
            model=None,
            resume_id=None,
            bare=True,
            interactive=False,
            output_format="text",
            system_prompt_override=None,
            max_tokens=None,
            classic=False,
        )

    assert warn_fn.call_count >= 1


def test_repl_persist_session_emits_runtime_warning_on_failure(tmp_path):
    from ccb.repl import REPLApp

    repl = REPLApp(
        version="test",
        model="m",
        cwd=str(tmp_path),
        provider=None,
        session=Session(id="s1", cwd=str(tmp_path), model="m"),
        registry=None,
        system_prompt="",
        state={"vim_mode": False},
    )

    with (
        patch("ccb.repl.save_session", side_effect=OSError("disk full")),
        patch("ccb.repl.emit_runtime_warning") as warn_fn,
    ):
        repl._persist_session("repl_test_persist_failed")

    warn_fn.assert_called_once()
