from unittest.mock import AsyncMock, patch

import pytest

from ccb.api.base import ToolCall
from ccb.loop import execute_tool_calls
from ccb.tools.ask_user import AskUserQuestionTool
from ccb.tools.base import ToolRegistry


@pytest.mark.asyncio
async def test_execute_tool_calls_rejects_invalid_schema_input_before_execution():
    registry = ToolRegistry()
    tool = AskUserQuestionTool()
    tool.execute = AsyncMock()
    registry.register(tool)

    tool_calls = [
        ToolCall(
            id="tc1",
            name="ask_user_question",
            input={"question": 123},
        )
    ]

    with patch("ccb.loop.needs_permission", return_value=False):
        results = await execute_tool_calls(tool_calls, registry, cwd=".")

    assert len(results) == 1
    assert results[0].is_error is True
    assert "Invalid tool input:" in results[0].content
    assert "Field 'question' must be a string, got int" in results[0].content
    tool.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_calls_does_not_prompt_permission_for_invalid_input():
    registry = ToolRegistry()
    registry.register(AskUserQuestionTool())

    tool_calls = [
        ToolCall(
            id="tc1",
            name="ask_user_question",
            input={"question": "请选择", "options": 42},
        )
    ]

    with (
        patch("ccb.loop.needs_permission", return_value=True) as needs_perm,
        patch("ccb.loop.ask_permission", new=AsyncMock()) as ask_permission,
    ):
        results = await execute_tool_calls(tool_calls, registry, cwd=".")

    assert len(results) == 1
    assert results[0].is_error is True
    assert "Invalid tool input:" in results[0].content
    needs_perm.assert_not_called()
    ask_permission.assert_not_awaited()
