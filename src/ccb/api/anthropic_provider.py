"""Anthropic Claude provider with native streaming + tool_use."""
from __future__ import annotations

from typing import Any, AsyncIterator

import anthropic

from ccb.api.base import Message, Provider, StreamEvent, ToolCall


class AnthropicProvider(Provider):
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self._model = model
        self._thinking_enabled = False
        self._thinking_budget = 10000
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)

    def name(self) -> str:
        return "anthropic"

    def set_thinking(self, enabled: bool, budget: int = 10000) -> None:
        """Enable/disable extended thinking."""
        self._thinking_enabled = enabled
        self._thinking_budget = max(1024, budget)

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        system: str = "",
        max_tokens: int = 16384,
    ) -> AsyncIterator[StreamEvent]:
        api_messages = [m.to_anthropic() for m in messages]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        # Extended thinking support
        if self._thinking_enabled:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget,
            }
            # Anthropic requires max_tokens >= budget_tokens + some margin
            if max_tokens < self._thinking_budget + 1024:
                kwargs["max_tokens"] = self._thinking_budget + 4096

        current_tool: dict[str, Any] | None = None
        input_json_buf = ""
        in_thinking = False

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool = {"id": block.id, "name": block.name}
                        input_json_buf = ""
                        yield StreamEvent(
                            type="tool_use_start",
                            tool_call=ToolCall(id=block.id, name=block.name, input={}),
                        )
                    elif block.type == "thinking":
                        in_thinking = True
                    elif block.type == "text":
                        pass  # text deltas come in content_block_delta

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield StreamEvent(type="text", text=delta.text)
                    elif delta.type == "thinking_delta":
                        yield StreamEvent(type="thinking", text=delta.thinking)
                    elif delta.type == "input_json_delta":
                        input_json_buf += delta.partial_json
                        yield StreamEvent(type="tool_use_input", text=delta.partial_json)

                elif event.type == "content_block_stop":
                    if in_thinking:
                        in_thinking = False
                    elif current_tool:
                        import json
                        try:
                            parsed_input = json.loads(input_json_buf) if input_json_buf else {}
                        except json.JSONDecodeError:
                            parsed_input = {}
                        tc = ToolCall(
                            id=current_tool["id"],
                            name=current_tool["name"],
                            input=parsed_input,
                        )
                        yield StreamEvent(type="tool_use_end", tool_call=tc)
                        current_tool = None
                        input_json_buf = ""

                elif event.type == "message_stop":
                    pass

            # Final message for usage
            final = await stream.get_final_message()
            yield StreamEvent(
                type="done",
                stop_reason=final.stop_reason,
                usage={
                    "input_tokens": final.usage.input_tokens,
                    "output_tokens": final.usage.output_tokens,
                },
            )

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert internal tool format to Anthropic API format."""
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
            }
            for t in tools
        ]
