from types import SimpleNamespace
from unittest.mock import patch

from prompt_toolkit.completion import Completion
from prompt_toolkit.buffer import CompletionState
from prompt_toolkit.document import Document
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

from ccb.repl import FOOTER_MAX_HEIGHT, INPUT_MAX_HEIGHT, REPLApp, STATUS_MAX_HEIGHT, STATUS_MIN_HEIGHT, accept_completion_or_submit
from ccb.display import _apply_left_border
from ccb.skills import Skill, SKILL_KIND


def _make_repl(tmp_path):
    return REPLApp(
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


def test_repl_input_wraps_and_can_grow(tmp_path):
    repl = _make_repl(tmp_path)

    assert repl._input_buffer.multiline()
    assert repl._input_window.wrap_lines()
    assert repl._input_window.height.min == 1
    assert repl._input_window.height.max == INPUT_MAX_HEIGHT
    assert repl._input_row.height is None


def test_slash_prefix_shows_ghost_suggestion(tmp_path):
    repl = _make_repl(tmp_path)
    repl._input_buffer.text = "/resu"
    repl._input_buffer.cursor_position = len("/resu")

    rendered = "".join(text for _, text in repl._input_prefix_fragments())

    assert rendered.startswith("› ")
    assert "/resume".endswith("/resu" + rendered[2:])


def test_plain_input_prefix_has_no_ghost_suggestion(tmp_path):
    repl = _make_repl(tmp_path)
    repl._input_buffer.text = "hello"
    repl._input_buffer.cursor_position = len("hello")

    rendered = "".join(text for _, text in repl._input_prefix_fragments())

    assert rendered == "› "


def test_slash_panel_stays_visible_for_partial_command(tmp_path):
    repl = _make_repl(tmp_path)
    repl.app.output.get_size = lambda: SimpleNamespace(rows=24, columns=80)
    repl._input_buffer.text = "/res"
    repl._input_buffer.cursor_position = len("/res")

    assert repl._should_show_slash_panel() is True

    rendered = "".join(text for _, text in repl._get_slash_panel_fragments())

    assert "Slash commands" in rendered
    assert "/resume" in rendered
    assert "Resume a saved session" in rendered


def test_slash_panel_hides_after_command_arguments_begin(tmp_path):
    repl = _make_repl(tmp_path)
    repl._input_buffer.text = "/resume abc123"
    repl._input_buffer.cursor_position = len("/resume abc123")

    assert repl._should_show_slash_panel() is False
    assert repl._get_slash_panel_fragments() == []


def test_skill_invocation_command_shows_in_slash_panel(tmp_path):
    skills = [Skill(name="review", description="Review code", prompt="p", source="bundled", kind=SKILL_KIND)]
    with patch("ccb.skills.build_skill_command_map", return_value={"/review": "[skill] Review code"}), patch(
        "ccb.skills.build_skill_invocation_map",
        return_value={"/skills review": "Review code"},
    ):
        repl = _make_repl(tmp_path)
    repl.app.output.get_size = lambda: SimpleNamespace(rows=24, columns=80)
    repl._input_buffer.text = "/skills re"
    repl._input_buffer.cursor_position = len("/skills re")

    rendered = "".join(text for _, text in repl._get_slash_panel_fragments())

    assert "/skills review" in rendered


def test_scrolled_history_is_top_aligned(tmp_path):
    repl = _make_repl(tmp_path)
    repl.app.output.get_size = lambda: SimpleNamespace(rows=20, columns=80)
    repl._msg_lines = [("", f"line {i}\n") for i in range(30)]
    repl._scroll_back = 28

    fragments = repl._get_message_fragments()

    assert fragments
    assert not fragments[0][1].startswith("\n")
    assert "line 0" in "".join(text for _, text in fragments)
    assert "line 5" in "".join(text for _, text in fragments)


def test_scrolled_history_clamps_to_top_without_header_or_blank_padding(tmp_path):
    repl = _make_repl(tmp_path)
    repl.app.output.get_size = lambda: SimpleNamespace(rows=12, columns=20)
    repl._msg_lines = [("", f"line {i}\n") for i in range(8)]
    repl._scroll_back = 20

    text = "".join(fragment for _, fragment in repl._get_message_fragments())

    assert text.startswith("line 0")
    assert "↑" not in text
    assert "\n\n" not in text
    assert text.splitlines()[:2] == ["line 0", "line 1"]


def test_hidden_below_counts_visual_rows_for_wrapped_lines(tmp_path):
    repl = _make_repl(tmp_path)
    repl.app.output.get_size = lambda: SimpleNamespace(rows=20, columns=20)
    repl._msg_lines = [("", f"line {i} " + "x" * 30 + "\n") for i in range(20)]
    repl._scroll_back = 5

    assert repl._hidden_below_visual_rows() == 5


def test_streaming_multiline_preserves_line_breaks(tmp_path):
    repl = _make_repl(tmp_path)
    repl._stream_text = "a\nb\nc"

    rendered = "".join(text for _, text in repl._build_message_parts()[-6:])

    assert rendered == "    a\n    b\n    c\n"


def test_reserved_layout_rows_accounts_for_multiline_footer(tmp_path):
    repl = _make_repl(tmp_path)
    with patch("ccb.config.get_permission_mode", return_value="default"), patch(
        "ccb.memory.get_store",
        return_value=SimpleNamespace(count=0),
    ), patch("ccb.memory.ProjectAdvisor") as advisor_cls, patch(
        "ccb.config.get_active_account",
        return_value=None,
    ):
        advisor = advisor_cls.return_value
        advisor.get_suggestions.return_value = ["Create CLAUDE.md to help AI understand your project"]
        rows = repl._reserved_layout_rows(80)

    reserved_non_footer = STATUS_MIN_HEIGHT + 2 + 1 + 1
    assert rows - reserved_non_footer == 0


def test_reserved_layout_rows_grows_with_multiline_input(tmp_path):
    repl = _make_repl(tmp_path)

    with patch(
        "ccb.memory.get_store",
        return_value=SimpleNamespace(count=0),
    ), patch("ccb.memory.ProjectAdvisor") as advisor_cls, patch(
        "ccb.config.get_active_account",
        return_value=None,
    ):
        advisor = advisor_cls.return_value
        advisor.get_suggestions.return_value = []
        base_rows = repl._reserved_layout_rows(80)
        repl._input_buffer.text = "\n".join(f"line {i}" for i in range(5))
        rows = repl._reserved_layout_rows(80)

    assert rows - base_rows == 4


def test_reserved_layout_rows_caps_status_growth(tmp_path):
    repl = _make_repl(tmp_path)
    repl.session = SimpleNamespace(
        model="test-model",
        last_input_tokens=1_500_000,
        total_output_tokens=1_500_000,
    )
    with patch("ccb.config.get_active_account", return_value={"_name": "acct"}), patch(
        "ccb.memory.get_store",
    ) as store_fn, patch("ccb.memory.ProjectAdvisor") as advisor_cls:
        store_fn.return_value = SimpleNamespace(count=15)
        advisor = advisor_cls.return_value
        advisor.get_suggestions.return_value = ["x" * 200]
        rows = repl._reserved_layout_rows(30)

    footer_rows = FOOTER_MAX_HEIGHT
    input_rows = 1
    reserved_non_status = 2 + footer_rows + input_rows
    assert STATUS_MIN_HEIGHT <= rows - reserved_non_status <= STATUS_MAX_HEIGHT


def test_reserved_layout_rows_caps_dense_footer_and_counts_scroll_pill(tmp_path):
    repl = _make_repl(tmp_path)
    repl._scroll_back = 5
    repl._pending_images = [{}, {}]
    repl._pending_files = [{}]
    repl.state["effort"] = "medium"
    repl.state["vim_mode"] = True

    with patch("ccb.config.get_permission_mode", return_value="bypassPermissions"), patch(
        "ccb.memory.get_store",
        return_value=SimpleNamespace(count=9),
    ), patch("ccb.config.get_active_account", return_value={"_name": "acct"}):
        footer = repl._get_footer_fragments()
        rows = repl._reserved_layout_rows(20)

    footer_rows = REPLApp._measure_fragment_rows(
        footer,
        20,
        min_rows=1,
        max_rows=FOOTER_MAX_HEIGHT,
    )
    reserved_non_footer = STATUS_MAX_HEIGHT + 2 + 1 + 1

    assert footer_rows == FOOTER_MAX_HEIGHT
    rendered = "".join(text for _, text in footer)
    assert rendered.count("\n") == 0
    assert " · attach 2 img, 1 file" in rendered
    assert rendered.endswith(" · medium · vim")
    assert rows - reserved_non_footer == FOOTER_MAX_HEIGHT


def test_footer_stays_single_line_when_pending_attachments_exist(tmp_path):
    repl = _make_repl(tmp_path)
    repl._pending_images = [{}, {}]
    repl._pending_files = [{}]

    footer = repl._get_footer_fragments()

    rendered = "".join(text for _, text in footer)
    assert "\n" not in rendered
    assert "attach 2 img, 1 file" in rendered


def test_footer_compacts_effort_and_vim_mode_into_single_line(tmp_path):
    repl = _make_repl(tmp_path)
    repl.state["effort"] = "medium"
    repl.state["vim_mode"] = True

    footer = repl._get_footer_fragments()

    rendered = "".join(text for _, text in footer)
    assert " · medium" in rendered
    assert " · vim" in rendered


def test_status_shortens_long_cwd_for_narrow_layout(tmp_path):
    repl = _make_repl(tmp_path)
    repl.cwd = "/Users/huaan/projects/very/long/path/to/a/deeply/nested/workspace"
    repl.app.output.get_size = lambda: SimpleNamespace(rows=20, columns=24)

    rendered = "".join(text for _, text in repl._get_status_fragments())

    assert "…" in rendered
    assert "orkspace" in rendered


def test_group_by_line_does_not_expand_single_trailing_newlines():
    groups = REPLApp._group_by_line([("", "line1\n")])

    assert groups == [[("", "line1")]]


def test_group_by_line_preserves_intentional_blank_lines():
    groups = REPLApp._group_by_line([("", "line1\n\n")])

    assert groups == [[("", "line1")], []]


def test_apply_left_border_blank_lines_follow_custom_prefix():
    bordered = _apply_left_border([("", "line1\n\nline2\n")], "class:x", border_char="    ")
    rendered = "".join(text for _, text in bordered)

    assert rendered == "    line1\n\n    line2\n"


def test_replay_header_keeps_single_blank_separator():
    groups = REPLApp._group_by_line([
        ("class:dim", "  -- Resumed session --\n\n"),
        ("", "\n"),
        ("label", "  You\n"),
        ("border", "  | "),
        ("user", "hello\n"),
    ])

    assert groups == [
        [("class:dim", "  -- Resumed session --")],
        [],
        [],
        [("label", "  You")],
        [("border", "  | "), ("user", "hello")],
    ]


def test_replay_session_history_limits_rendered_messages(tmp_path):
    repl = _make_repl(tmp_path)
    session = SimpleNamespace(
        messages=[SimpleNamespace(role=SimpleNamespace(value="user"), content=f"msg {i}", images=[], files=[], media=[], tool_results=[]) for i in range(90)],
        id="session-1",
        model="test-model",
    )
    repl.session = session

    from ccb.api.base import Role
    for msg in repl.session.messages:
        msg.role = Role.USER

    repl._replay_session_history()
    rendered = "".join(text for _, text in repl._msg_lines)

    assert "showing last 80; skipped 10 older messages" in rendered
    assert "msg 0" not in rendered
    assert "msg 10" in rendered
    assert "msg 89" in rendered


def test_enter_binding_submits_when_no_completion():
    calls = []

    class Buffer:
        complete_state = None

        def validate_and_handle(self):
            calls.append("submit")

    accept_completion_or_submit(SimpleNamespace(current_buffer=Buffer()))

    assert calls == ["submit"]


def test_enter_binding_accepts_selected_completion_first():
    completion = Completion("/help", start_position=-2)
    state = CompletionState(Document("/h"), [completion], complete_index=0)
    calls = []

    class Buffer:
        complete_state = state

        def apply_completion(self, selected):
            calls.append(selected)

        def validate_and_handle(self):
            calls.append("submit")

    accept_completion_or_submit(SimpleNamespace(current_buffer=Buffer()))

    assert calls == [completion]


def test_scroll_by_visual_rows_clamps_to_hidden_content(tmp_path):
    repl = _make_repl(tmp_path)
    repl.app.output.get_size = lambda: SimpleNamespace(rows=20, columns=20)
    repl._msg_lines = [("", f"line {i} " + "x" * 30 + "\n") for i in range(20)]

    repl._scroll_by_visual_rows(999)
    text = "".join(fragment for _, fragment in repl._get_message_fragments())

    assert repl._scroll_back == repl._max_scroll_back()
    assert text.startswith("line 0 ")
    assert "↑" not in text


def test_mouse_wheel_scrolls_history_by_visual_step(tmp_path):
    repl = _make_repl(tmp_path)
    repl.app.output.get_size = lambda: SimpleNamespace(rows=20, columns=20)
    repl._msg_lines = [("", f"line {i} " + "x" * 30 + "\n") for i in range(20)]

    scroll_up = MouseEvent(
        position=SimpleNamespace(x=0, y=0),
        event_type=MouseEventType.SCROLL_UP,
        button=MouseButton.NONE,
        modifiers=(),
    )
    scroll_down = MouseEvent(
        position=SimpleNamespace(x=0, y=0),
        event_type=MouseEventType.SCROLL_DOWN,
        button=MouseButton.NONE,
        modifiers=(),
    )

    result_up = repl._message_control.mouse_handler(scroll_up)
    scrolled_back = repl._scroll_back
    result_down = repl._message_control.mouse_handler(scroll_down)

    assert result_up is None
    assert scrolled_back == repl._wheel_scroll_step()
    assert result_down is None
    assert repl._scroll_back == 0


def test_should_use_pager_for_text_heavy_commands():
    assert REPLApp._should_use_pager("/help") is True
    assert REPLApp._should_use_pager("/history") is True
    assert REPLApp._should_use_pager("/plugin list") is True
    assert REPLApp._should_use_pager("/plugin marketplace") is True
    assert REPLApp._should_use_pager("/plugin marketplace list") is True


def test_should_not_use_pager_for_interactive_or_mutating_plugin_commands():
    assert REPLApp._should_use_pager("/plugin install demo") is False
    assert REPLApp._should_use_pager("/plugin browse") is False
    assert REPLApp._should_use_pager("/model") is False


def test_nested_overlay_restores_erase_when_done_state(tmp_path):
    repl = _make_repl(tmp_path)
    repl.app.erase_when_done = True

    repl.enter_nested_overlay()

    assert repl._nested_app_active is True
    assert repl.app.erase_when_done is False

    repl.exit_nested_overlay()

    assert repl._nested_app_active is False
    assert repl.app.erase_when_done is True
    assert repl._nested_prev_erase_when_done is None
