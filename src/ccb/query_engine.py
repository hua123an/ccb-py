"""Non-interactive query engine for ccb-py.

Supports ``ccb -p "prompt"`` pipe mode: reads prompt from args or
stdin, sends to the model, prints the response, and exits.
Also handles ``/pipes`` for multi-step pipe chains.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any, AsyncIterator


async def run_query(
    prompt: str,
    model: str | None = None,
    provider_name: str | None = None,
    system_prompt: str | None = None,
    max_tokens: int = 4096,
    output_format: str = "text",  # text, json, markdown
    temperature: float | None = None,
    tools: bool = False,
    cwd: str | None = None,
) -> str:
    """Run a single non-interactive query and return the response text."""
    from ccb.api.base import Message, Role
    from ccb.api.router import create_provider
    from ccb.config import load_config

    cfg = load_config()
    model = model or cfg.get("model", "")
    provider_name = provider_name or cfg.get("provider", "anthropic")

    provider = create_provider(provider_name, model=model, cwd=cwd)

    messages = [Message(role=Role.USER, content=prompt)]

    sys_prompt = system_prompt
    if not sys_prompt:
        if output_format == "json":
            sys_prompt = "You are a helpful assistant. Respond with valid JSON only."
        elif output_format == "markdown":
            sys_prompt = "You are a helpful assistant. Format your response in Markdown."
        else:
            sys_prompt = "You are a helpful assistant. Be concise and direct."

    full_text = ""
    async for event in provider.stream(
        messages=messages,
        tools=[],
        system=sys_prompt,
        max_tokens=max_tokens,
    ):
        if event.type == "text":
            full_text += event.text

    return full_text.strip()


async def run_query_streaming(
    prompt: str,
    model: str | None = None,
    provider_name: str | None = None,
    system_prompt: str | None = None,
    max_tokens: int = 4096,
    cwd: str | None = None,
) -> AsyncIterator[str]:
    """Run a query with streaming output — yields text chunks."""
    from ccb.api.base import Message, Role
    from ccb.api.router import create_provider
    from ccb.config import load_config

    cfg = load_config()
    model = model or cfg.get("model", "")
    provider_name = provider_name or cfg.get("provider", "anthropic")

    provider = create_provider(provider_name, model=model, cwd=cwd)
    messages = [Message(role=Role.USER, content=prompt)]

    sys_prompt = system_prompt or "You are a helpful assistant. Be concise and direct."

    async for event in provider.stream(
        messages=messages,
        tools=[],
        system=sys_prompt,
        max_tokens=max_tokens,
    ):
        if event.type == "text":
            yield event.text


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
