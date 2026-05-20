"""Non-interactive query engine for ccb-py.

Supports ``ccb -p "prompt"`` pipe mode: reads prompt from args or
stdin, sends to the model, prints the response, and exits.
Also handles ``/pipes`` for multi-step pipe chains.
"""
from __future__ import annotations

import os
import sys
from typing import AsyncIterator

from ccb.api.base import Message
from ccb.session import Session


def _build_default_system_prompt(cwd: str, model: str, output_format: str = "text") -> str:
    """Build a cwd-aware default system prompt for non-interactive queries."""
    from ccb.prompts import get_system_prompt

    base_prompt = get_system_prompt(cwd, model=model)
    if output_format == "json":
        format_instruction = "Respond with valid JSON only."
    elif output_format == "markdown":
        format_instruction = "Format your response in Markdown."
    else:
        format_instruction = "Be concise and direct."
    return f"{base_prompt}\n\n# Response format\n{format_instruction}"


async def run_query(
    prompt: str,
    model: str | None = None,
    provider_name: str | None = None,
    system_prompt: str | None = None,
    max_tokens: int = 4096,
    output_format: str = "text",  # text, json, markdown
    temperature: float | None = None,
    cwd: str | None = None,
    messages: list[Message] | None = None,
    session: Session | None = None,
) -> str:
    """Run a single non-interactive query and return the response text."""
    from ccb.api.base import Role
    from ccb.api.router import create_provider
    from ccb.config import load_global_config

    cfg = load_global_config()
    model = model or cfg.get("model", "")
    effective_cwd = cwd or os.getcwd()

    provider = create_provider(model=model, provider_type=provider_name)
    from ccb.cost_tracker import get_cost_state
    cost = get_cost_state()
    cost.set_model(model)
    cost.start_turn()

    query_messages = messages or [Message(role=Role.USER, content=prompt)]

    sys_prompt = system_prompt or _build_default_system_prompt(
        effective_cwd,
        model,
        output_format=output_format,
    )

    full_text = ""
    usage: dict[str, int] = {}
    async for event in provider.stream(
        messages=query_messages,
        tools=[],
        system=sys_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    ):
        if event.type == "text":
            full_text += event.text
        elif event.type == "done":
            usage = event.usage or {}

    if usage:
        cost.add_usage(usage)
        if session is not None:
            session.add_usage(usage)
    cost.end_turn()

    return full_text.strip()


async def run_query_streaming(
    prompt: str,
    model: str | None = None,
    provider_name: str | None = None,
    system_prompt: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
    cwd: str | None = None,
    messages: list[Message] | None = None,
    session: Session | None = None,
) -> AsyncIterator[str]:
    """Run a query with streaming output — yields text chunks."""
    from ccb.api.base import Role
    from ccb.api.router import create_provider
    from ccb.config import load_global_config

    cfg = load_global_config()
    model = model or cfg.get("model", "")
    effective_cwd = cwd or os.getcwd()

    provider = create_provider(model=model, provider_type=provider_name)
    query_messages = messages or [Message(role=Role.USER, content=prompt)]
    from ccb.cost_tracker import get_cost_state
    cost = get_cost_state()
    cost.set_model(model)
    cost.start_turn()

    sys_prompt = system_prompt or _build_default_system_prompt(effective_cwd, model)

    usage: dict[str, int] = {}
    async for event in provider.stream(
        messages=query_messages,
        tools=[],
        system=sys_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    ):
        if event.type == "text":
            yield event.text
        elif event.type == "done":
            usage = event.usage or {}

    if usage:
        cost.add_usage(usage)
        if session is not None:
            session.add_usage(usage)
    cost.end_turn()


async def pipe_mode(
    prompt: str | None = None,
    stdin_data: str | None = None,
    model: str | None = None,
    provider_name: str | None = None,
    output_format: str = "text",
    cwd: str | None = None,
) -> int:
    """Main entry point for pipe mode (-p flag). Returns exit code."""
    # Build prompt from args + stdin
    parts = []
    if stdin_data:
        parts.append(f"Input data:\n```\n{stdin_data}\n```\n")
    if prompt:
        parts.append(prompt)
    if not parts:
        print("Error: no prompt provided", file=sys.stderr)
        return 1

    full_prompt = "\n".join(parts)

    try:
        result = await run_query(
            full_prompt,
            model=model,
            provider_name=provider_name,
            output_format=output_format,
            cwd=cwd,
        )
        print(result)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Pipe chains
# ---------------------------------------------------------------------------

class PipeChain:
    """Multi-step prompt pipeline.

    Example:
        chain = PipeChain()
        chain.add("Summarize the following code")
        chain.add("Extract the main function names from the summary")
        result = await chain.execute(input_text)
    """

    def __init__(self, model: str | None = None, provider: str | None = None):
        self._steps: list[str] = []
        self._model = model
        self._provider = provider

    def add(self, prompt_template: str) -> "PipeChain":
        self._steps.append(prompt_template)
        return self

    async def execute(self, initial_input: str = "") -> str:
        current = initial_input
        for i, step in enumerate(self._steps):
            prompt = step
            if current:
                prompt = f"{step}\n\nInput:\n{current}"
            current = await run_query(
                prompt,
                model=self._model,
                provider_name=self._provider,
            )
        return current

    def __len__(self) -> int:
        return len(self._steps)
