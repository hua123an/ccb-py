from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ccb.tools.ask_user import AskUserQuestionTool, _extract_embedded_options, _normalize_options


def test_extract_embedded_numbered_options():
    question, options = _extract_embedded_options(
        "你想怎么处理这个问题？\n"
        "1. 直接修复并继续\n"
        "2. 先给我总结现状\n"
        "3. 暂时跳过"
    )

    assert question == "你想怎么处理这个问题？"
    assert options == ["直接修复并继续", "先给我总结现状", "暂时跳过"]


def test_extract_embedded_options_rejects_mixed_body_text():
    original = "请选择一个操作：\n1. 修复\n这里是额外说明\n2. 跳过"

    question, options = _extract_embedded_options(original)

    assert question == original
    assert options == []


def test_normalize_options_preserves_object_fields():
    options = _normalize_options(
        [
            {"label": "直接修复", "value": "fix_now", "description": "立即继续处理"},
            {"value": "summarize_first"},
            "跳过",
        ]
    )

    assert options == [
        {"label": "直接修复", "value": "fix_now", "description": "立即继续处理"},
        {"label": "summarize_first", "value": "summarize_first", "description": ""},
        {"label": "跳过", "value": "跳过", "description": ""},
    ]


@pytest.mark.asyncio
async def test_execute_uses_repl_selection_when_options_are_structured():
    tool = AskUserQuestionTool()
    repl = SimpleNamespace()
    repl.ask_user_question_async = AsyncMock(return_value="继续")

    with patch("ccb.repl.get_active_repl", return_value=repl):
        result = await tool.execute(
            {
                "question": "请选择下一步",
                "options": ["继续", "停止"],
            },
            ".",
        )

    repl.ask_user_question_async.assert_awaited_once_with(
        "请选择下一步",
        [
            {"label": "继续", "value": "继续", "description": ""},
            {"label": "停止", "value": "停止", "description": ""},
        ],
    )
    assert result.output == "继续"


@pytest.mark.asyncio
async def test_execute_extracts_embedded_options_for_repl_selection():
    tool = AskUserQuestionTool()
    repl = SimpleNamespace()
    repl.ask_user_question_async = AsyncMock(return_value="直接修复")

    with patch("ccb.repl.get_active_repl", return_value=repl):
        result = await tool.execute(
            {
                "question": (
                    "请选择处理方式：\n"
                    "1. 直接修复\n"
                    "2. 先解释原因\n"
                    "3. 暂时跳过"
                ),
            },
            ".",
        )

    repl.ask_user_question_async.assert_awaited_once_with(
        "请选择处理方式：",
        [
            {"label": "直接修复", "value": "直接修复", "description": ""},
            {"label": "先解释原因", "value": "先解释原因", "description": ""},
            {"label": "暂时跳过", "value": "暂时跳过", "description": ""},
        ],
    )
    assert result.output == "直接修复"


@pytest.mark.asyncio
async def test_execute_preserves_structured_options_for_repl_selection():
    tool = AskUserQuestionTool()
    repl = SimpleNamespace()
    repl.ask_user_question_async = AsyncMock(return_value="fix_now")

    with patch("ccb.repl.get_active_repl", return_value=repl):
        result = await tool.execute(
            {
                "question": "请选择处理方式",
                "options": [
                    {"label": "直接修复", "value": "fix_now", "description": "立即继续处理"},
                    {"label": "先解释原因", "value": "explain_first"},
                ],
            },
            ".",
        )

    repl.ask_user_question_async.assert_awaited_once_with(
        "请选择处理方式",
        [
            {"label": "直接修复", "value": "fix_now", "description": "立即继续处理"},
            {"label": "先解释原因", "value": "explain_first", "description": ""},
        ],
    )
    assert result.output == "fix_now"


@pytest.mark.asyncio
async def test_repl_question_options_include_other_custom_input(tmp_path):
    from ccb.repl import REPLApp

    repl = REPLApp(
        version="test",
        model="test-model",
        cwd=str(tmp_path),
        provider=None,
        session=SimpleNamespace(
            model="test-model",
            last_input_tokens=0,
            total_output_tokens=0,
        ),
        registry=None,
        system_prompt="",
        state={"vim_mode": False},
    )

    with (
        patch("ccb.select_ui.select_one", AsyncMock(return_value=2)) as select_one,
        patch("ccb.select_ui.ask_text", AsyncMock(return_value="  自定义答案  ")) as ask_text,
    ):
        answer = await repl.ask_user_question_async("请选择下一步", ["继续", "停止"])

    select_items = select_one.await_args.args[0]
    assert [item["label"] for item in select_items] == ["继续", "停止", "Other"]
    ask_text.assert_awaited_once_with(
        "Your answer",
        placeholder="Type your own response",
        title="请选择下一步",
    )
    assert answer == "自定义答案"


@pytest.mark.asyncio
async def test_repl_question_other_cancel_returns_user_skipped(tmp_path):
    from ccb.repl import REPLApp

    repl = REPLApp(
        version="test",
        model="test-model",
        cwd=str(tmp_path),
        provider=None,
        session=SimpleNamespace(
            model="test-model",
            last_input_tokens=0,
            total_output_tokens=0,
        ),
        registry=None,
        system_prompt="",
        state={"vim_mode": False},
    )

    with (
        patch("ccb.select_ui.select_one", AsyncMock(return_value=1)),
        patch("ccb.select_ui.ask_text", AsyncMock(return_value=None)),
    ):
        answer = await repl.ask_user_question_async("请选择下一步", ["继续"])

    assert answer == "(user skipped)"


@pytest.mark.asyncio
async def test_repl_question_returns_option_value_for_structured_options(tmp_path):
    from ccb.repl import REPLApp

    repl = REPLApp(
        version="test",
        model="test-model",
        cwd=str(tmp_path),
        provider=None,
        session=SimpleNamespace(
            model="test-model",
            last_input_tokens=0,
            total_output_tokens=0,
        ),
        registry=None,
        system_prompt="",
        state={"vim_mode": False},
    )

    with patch("ccb.select_ui.select_one", AsyncMock(return_value=0)):
        answer = await repl.ask_user_question_async(
            "请选择下一步",
            [
                {"label": "继续", "value": "continue", "description": "马上执行"},
                {"label": "停止", "value": "stop", "description": ""},
            ],
        )

    assert answer == "continue"
