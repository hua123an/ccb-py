from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccb.api.base import Message, Role
from ccb.session import Session


@pytest.mark.asyncio
async def test_apply_context_management_collapses_before_compaction():
    from ccb.loop import _apply_context_management

    session = Session(id="s1", cwd="/tmp/project", model="m")
    session.messages = [Message(role=Role.USER, content="hello")]
    session.last_input_tokens = 800
    provider = MagicMock()
    collapsed = [Message(role=Role.ASSISTANT, content="collapsed")]
    calls: list[str] = []

    def _collapse(messages, model="", context_limit=0):
        calls.append("collapse")
        return collapsed

    async def _compact(target_session, _provider):
        calls.append("compact")
        assert target_session.messages == collapsed
        return 1

    with (
        patch("ccb.feature_flags.is_feature_enabled", return_value=True),
        patch("ccb.context_collapse.apply_collapses_if_needed", side_effect=_collapse),
        patch("ccb.memory.get_store") as get_store,
        patch("ccb.compaction.compact_session", side_effect=_compact),
        patch("ccb.session.Session.save"),
    ):
        get_store.return_value.check_offload_threshold.return_value = (True, "Mild offload")
        await _apply_context_management(provider, session, model_name="m", ctx_limit=1000)

    assert calls == ["collapse", "compact"]
    assert session.messages == collapsed
    assert session.last_input_tokens == 0


@pytest.mark.asyncio
async def test_apply_context_management_skips_compaction_below_threshold():
    from ccb.loop import _apply_context_management

    session = Session(id="s1", cwd="/tmp/project", model="m")
    session.messages = [Message(role=Role.USER, content="hello")]
    session.last_input_tokens = 100

    with (
        patch("ccb.feature_flags.is_feature_enabled", return_value=False),
        patch("ccb.memory.get_store") as get_store,
        patch("ccb.compaction.compact_session", new_callable=AsyncMock) as compact_session,
    ):
        get_store.return_value.check_offload_threshold.return_value = (False, "")
        await _apply_context_management(MagicMock(), session, model_name="m", ctx_limit=1000)

    compact_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_memories_uses_latest_natural_user_message():
    from ccb.api.base import ToolResult
    from ccb.loop import _extract_memories_from_latest_user_message

    session = Session(id="s1", cwd="/tmp/project", model="m")
    session.messages = [
        Message(role=Role.USER, content="first short"),
        Message(role=Role.ASSISTANT, content="reply"),
        Message(role=Role.USER, content="this is a sufficiently important user preference that should be remembered"),
        Message(role=Role.USER, tool_results=[ToolResult(tool_use_id="t1", content="done")]),
    ]

    extractor = MagicMock()
    extractor.set_provider = MagicMock()
    extractor.extract_from_message = AsyncMock()

    with patch("ccb.memory.get_extractor", return_value=extractor):
        await _extract_memories_from_latest_user_message(MagicMock(), session)

    extractor.set_provider.assert_called_once()
    extractor.extract_from_message.assert_awaited_once()
    args, kwargs = extractor.extract_from_message.await_args
    assert "sufficiently important user preference" in args[0]
    assert kwargs["role"] == "user"
