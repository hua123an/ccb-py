from ccb.display import _summarize_tool_input


def test_ask_user_summary_counts_list_options():
    summary = _summarize_tool_input(
        "ask_user_question",
        {
            "question": "请选择下一步",
            "options": ["继续", "暂停", "停止"],
        },
    )

    assert summary == "请选择下一步 (3 options)"


def test_ask_user_summary_counts_object_options():
    summary = _summarize_tool_input(
        "ask_user_question",
        {
            "question": "请选择处理方式",
            "options": [
                {"label": "直接修复", "description": "立即继续"},
                {"value": "先分析"},
            ],
        },
    )

    assert summary == "请选择处理方式 (2 options)"
