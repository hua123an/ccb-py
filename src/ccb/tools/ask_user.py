"""AskUserQuestion tool - lets the model ask the user a question interactively."""
from __future__ import annotations

import re
from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import ASK_USER_QUESTION_PROMPT


_QUESTION_OPTION_PATTERN = re.compile(
    r"^\s*(?:[-*•]\s+|(?:\d+|[A-Za-z])[.)、:：]\s+)(.+?)\s*$"
)


def _normalize_options(raw_options: Any) -> list[dict[str, str]]:
    """Normalize supported option shapes into label/value/description items."""
    options: list[dict[str, str]] = []

    def _append(label: Any, value: Any = "", description: Any = "") -> None:
        clean_label = str(label or "").strip()
        clean_value = str(value or "").strip()
        clean_desc = str(description or "").strip()
        if not clean_label:
            return
        options.append(
            {
                "label": clean_label,
                "value": clean_value or clean_label,
                "description": clean_desc if clean_desc and clean_desc != clean_label else "",
            }
        )

    if isinstance(raw_options, str):
        for item in raw_options.split(","):
            _append(item)
        return options

    if not isinstance(raw_options, list):
        return options

    for item in raw_options:
        if isinstance(item, dict):
            _append(
                item.get("label") or item.get("value") or item.get("description"),
                item.get("value") or item.get("label"),
                item.get("description"),
            )
        elif item is not None:
            _append(item)
    return options


def _extract_embedded_options(question: str) -> tuple[str, list[str]]:
    """Extract numbered/bulleted options embedded in question text."""
    lines = [line.rstrip() for line in question.splitlines()]
    title_lines: list[str] = []
    option_lines: list[str] = []
    in_options = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        match = _QUESTION_OPTION_PATTERN.match(line)
        if match:
            option_lines.append(match.group(1).strip())
            in_options = True
            continue
        if in_options:
            return question, []
        title_lines.append(line)

    unique_options: list[str] = []
    for option in option_lines:
        if option and option not in unique_options:
            unique_options.append(option)

    if len(unique_options) < 2:
        return question, []

    title = "\n".join(title_lines).strip() or question.strip()
    return title, unique_options


class AskUserQuestionTool(Tool):
    name = "ask_user_question"
    description = ASK_USER_QUESTION_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user.",
            },
            "options": {
                "description": "Optional choices for the user. Prefer an array of strings; comma-separated text is also accepted for compatibility.",
                "oneOf": [
                    {
                        "type": "array",
                        "items": {
                            "oneOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "value": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                },
                            ],
                        },
                    },
                    {
                        "type": "string",
                    },
                ],
            },
        },
        "required": ["question"],
    }

    @property
    def needs_permission(self) -> bool:
        return False

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        from ccb.display import console

        question = input.get("question", "")
        options = _normalize_options(input.get("options", ""))
        if not options and isinstance(question, str):
            question, embedded = _extract_embedded_options(question)
            options = _normalize_options(embedded)

        # Try REPL's async input (works inside prompt_toolkit TUI).
        # In fullscreen REPL, console.print() writes to raw terminal and
        # gets overwritten — we must render into the REPL's message buffer.
        try:
            from ccb.repl import get_active_repl
            repl = get_active_repl()
            if repl is not None:
                answer = await repl.ask_user_question_async(question, options)
                return ToolResult(output=answer)
        except Exception:
            pass

        # Fallback: plain console output (non-TUI mode)
        console.print(f"\n  [bold yellow]❓ {question}[/bold yellow]")
        if options:
            for i, opt in enumerate(options, 1):
                suffix = f" — {opt['description']}" if opt["description"] else ""
                console.print(f"    {i}. {opt['label']}{suffix}")
        try:
            answer = console.input("  [dim]Your answer >[/dim] ").strip()
            if options and answer.isdigit():
                idx = int(answer) - 1
                if 0 <= idx < len(options):
                    answer = options[idx]["value"]
            return ToolResult(output=answer if answer else "(no response)")
        except (EOFError, KeyboardInterrupt):
            return ToolResult(output="(user skipped)")
