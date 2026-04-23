"""Conversation compaction — mirrors official compact/prompt.ts.

Provides structured summarization with <analysis>+<summary> format,
9-section output, and post-processing to strip the analysis scratchpad.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Analysis instruction (shared preamble for the LLM's drafting step)
# ---------------------------------------------------------------------------
_ANALYSIS_INSTRUCTION = """\
Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly."""


# ---------------------------------------------------------------------------
# No-tools preamble — prevent the model from calling tools during compact
# ---------------------------------------------------------------------------
_NO_TOOLS_PREAMBLE = """\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use file_read, bash, grep, glob, file_edit, file_write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

_NO_TOOLS_TRAILER = (
    "\n\nREMINDER: Do NOT call any tools. Respond with plain text only — "
    "an <analysis> block followed by a <summary> block. "
    "Tool calls will be rejected and you will fail the task."
)


# ---------------------------------------------------------------------------
# Full compact prompt (summarize entire conversation)
# ---------------------------------------------------------------------------
_BASE_COMPACT_PROMPT = f"""\
Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

{_ANALYSIS_INSTRUCTION}

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages:
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response.

There may be additional summarization instructions provided in the included context. If so, remember to follow these instructions when creating the above summary. Examples of instructions include:
<example>
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also remember the mistakes you made and how you fixed them.
</example>

<example>
# Summary instructions
When you are using compact - please focus on test output and code changes. Include file reads verbatim.
</example>
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_compact_prompt(custom_instructions: str = "") -> str:
    """Return the full compact prompt (for summarizing an entire conversation)."""
    prompt = _NO_TOOLS_PREAMBLE + _BASE_COMPACT_PROMPT
    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nAdditional Instructions:\n{custom_instructions}"
    prompt += _NO_TOOLS_TRAILER
    return prompt


def get_compact_system() -> str:
    """System message for the compaction call."""
    return (
        "You are a conversation summarizer. Create a detailed, structured summary "
        "preserving all technical details needed to continue development work. "
        "Respond with TEXT ONLY — do NOT call any tools."
    )


def format_compact_summary(summary: str) -> str:
    """Strip <analysis> scratchpad and format the <summary> section.

    The analysis block improves summary quality during generation but has
    no value afterwards — strip it before injecting back into context.
    """
    formatted = summary

    # Strip analysis section
    formatted = re.sub(r"<analysis>[\s\S]*?</analysis>", "", formatted)

    # Extract and format summary section
    match = re.search(r"<summary>([\s\S]*?)</summary>", formatted)
    if match:
        content = match.group(1).strip()
        formatted = re.sub(r"<summary>[\s\S]*?</summary>", f"Summary:\n{content}", formatted)

    # Clean up extra whitespace
    formatted = re.sub(r"\n\n+", "\n\n", formatted)

    return formatted.strip()


def get_compact_user_message(summary: str, suppress_follow_up: bool = False) -> str:
    """Build the user-role message injected after compaction.

    This becomes the first message in the compacted conversation so that the
    model has full context of what happened before.
    """
    formatted = format_compact_summary(summary)

    base = (
        "This session is being continued from a previous conversation that ran out "
        "of context. The summary below covers the earlier portion of the conversation.\n\n"
        f"{formatted}"
    )

    if suppress_follow_up:
        base += (
            "\n\nContinue the conversation from where it left off without asking the "
            "user any further questions. Resume directly — do not acknowledge the summary, "
            "do not recap what was happening, do not preface with \"I'll continue\" or "
            "similar. Pick up the last task as if the break never happened."
        )

    return base


# ---------------------------------------------------------------------------
# Multi-round progressive compaction
# ---------------------------------------------------------------------------

class CompactEngine:
    """Advanced compaction with multi-round and tool-prompt slimming."""

    def __init__(self, provider: Any = None) -> None:
        self._provider = provider
        self._round = 0
        self._history: list[str] = []

    async def compact_progressive(
        self,
        messages: list[Any],
        max_rounds: int = 3,
        target_ratio: float = 0.3,
        custom_instructions: str = "",
    ) -> str:
        """Multi-round progressive compaction.

        Each round summarizes the previous summary + new context until
        the target compression ratio is reached.
        """
        from ccb.api.base import Message, Role

        full_text = "\n".join(
            f"[{m.role.value}]: {m.content[:2000]}" for m in messages if m.content
        )
        original_len = len(full_text)
        current = full_text

        for round_num in range(max_rounds):
            self._round = round_num + 1
            prompt = get_compact_prompt(custom_instructions)
            if round_num > 0:
                prompt = (
                    f"This is round {round_num + 1} of progressive compaction.\n"
                    f"The previous summary is below. Please create an even more "
                    f"concise version while preserving all critical technical details.\n\n"
                    f"Previous summary:\n{current}\n\n" + prompt
                )

            if self._provider:
                summary_messages = [Message(role=Role.USER, content=f"{current}\n\n{prompt}")]
                result = ""
                async for event in self._provider.stream(
                    messages=summary_messages,
                    tools=[],
                    system=get_compact_system(),
                    max_tokens=4096,
                ):
                    if event.type == "text":
                        result += event.text
                current = format_compact_summary(result)
            else:
                # Without provider, just truncate
                current = current[:int(len(current) * target_ratio)]

            self._history.append(current)

            # Check if we've hit target ratio
            if len(current) / original_len <= target_ratio:
                break

        return current

    def slim_tool_prompts(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Reduce tool prompt verbosity to save context tokens."""
        slimmed = []
        for tool in tools:
            slim = dict(tool)
            desc = slim.get("description", "")
            if len(desc) > 200:
                # Keep first sentence + truncate
                first_period = desc.find(". ")
                if first_period > 0 and first_period < 200:
                    slim["description"] = desc[:first_period + 1]
                else:
                    slim["description"] = desc[:200] + "..."
            # Slim input schema descriptions
            schema = slim.get("input_schema", {})
            props = schema.get("properties", {})
            for prop_name, prop_val in props.items():
                if isinstance(prop_val, dict) and len(prop_val.get("description", "")) > 100:
                    prop_val["description"] = prop_val["description"][:100] + "..."
            slimmed.append(slim)
        return slimmed

    def estimate_context_usage(self, messages: list[Any], tool_count: int = 0) -> dict[str, Any]:
        """Estimate context window usage."""
        msg_chars = sum(len(m.content or "") for m in messages)
        msg_tokens_est = msg_chars // 4
        tool_tokens_est = tool_count * 500  # rough estimate
        total = msg_tokens_est + tool_tokens_est
        return {
            "message_tokens": msg_tokens_est,
            "tool_tokens": tool_tokens_est,
            "total_tokens": total,
            "messages": len(messages),
            "tools": tool_count,
        }

    @property
    def rounds_completed(self) -> int:
        return self._round

    @property
    def history(self) -> list[str]:
        return self._history
