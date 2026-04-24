"""AskUserQuestion tool - lets the model ask the user a question interactively."""
from __future__ import annotations

from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import ASK_USER_QUESTION_PROMPT


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
                "type": "string",
                "description": "Optional comma-separated list of choices for the user.",
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
        options_str = input.get("options", "")
        options = [o.strip() for o in options_str.split(",") if o.strip()] if options_str else []

        console.print(f"\n  [bold yellow]❓ {question}[/bold yellow]")
        if options:
            for i, opt in enumerate(options, 1):
                console.print(f"    {i}. {opt}")

        # Try REPL's async input (works inside prompt_toolkit TUI)
        try:
            from ccb.repl import get_active_repl
            repl = get_active_repl()
            if repl is not None:
                answer = await repl.ask_user_question_async(question, options)
                console.print(f"  [dim]→ {answer}[/dim]")
                return ToolResult(output=answer)
        except Exception:
            pass

        # Fallback: plain console input (non-TUI mode)
        try:
            answer = console.input("  [dim]Your answer >[/dim] ").strip()
            if options and answer.isdigit():
                idx = int(answer) - 1
                if 0 <= idx < len(options):
                    answer = options[idx]
            return ToolResult(output=answer if answer else "(no response)")
        except (EOFError, KeyboardInterrupt):
            return ToolResult(output="(user skipped)")
