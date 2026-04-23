"""OpenAI-compatible provider (GPT, Grok, local models, etc.)."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from ccb.api.base import Message, Provider, StreamEvent, ToolCall


class OpenAIProvider(Provider):
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self._model = model
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)

    def name(self) -> str:
        return "openai"

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        system: str = "",
        max_tokens: int = 16384,
    ) -> AsyncIterator[StreamEvent]:
        api_messages = self._build_messages(messages, system)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            kwargs["tool_choice"] = "auto"

        # Track tool calls being assembled across chunks
        tool_calls_buf: dict[int, dict[str, Any]] = {}
        total_usage = {"input_tokens": 0, "output_tokens": 0}
        finish_reason: str | None = None

        # Debug log: record exact model + base_url actually sent. Useful when
        # a relay (e.g. huaan.space, b.ai) mislabels the backing model in the
        # response — you can confirm ccb sent the correct model string.
        import os
        if os.environ.get("CCB_DEBUG"):
            from pathlib import Path
            import time as _time
            try:
                log = Path.home() / ".claude" / "ccb-debug.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                base_url = getattr(self._client, "base_url", "?")
                with log.open("a") as f:
                    f.write(
                        f"[{_time.strftime('%H:%M:%S')}] openai_provider.stream "
                        f"model={self._model!r} base_url={base_url!r} "
                        f"msgs={len(api_messages)} tools={len(tools) if tools else 0}\n"
                    )
            except Exception:
                pass

        # Some OpenAI-compatible APIs don't support stream_options;
        # fall back without it on error.
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception:
            kwargs.pop("stream_options", None)
            response = await self._client.chat.completions.create(**kwargs)
        async for chunk in response:
            # Usage arrives in the final chunk (empty choices) when
            # stream_options.include_usage is true.
            if chunk.usage:
                total_usage["input_tokens"] = chunk.usage.prompt_tokens or 0
                total_usage["output_tokens"] = chunk.usage.completion_tokens or 0

            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue

            delta = choice.delta

            # Text content
            if delta.content:
                yield StreamEvent(type="text", text=delta.content)

            # Tool calls (streamed incrementally)
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_buf:
                        tool_calls_buf[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                        if tc_delta.id:
                            tool_calls_buf[idx]["id"] = tc_delta.id
                    buf = tool_calls_buf[idx]
                    if tc_delta.function:
                        if tc_delta.function.name:
                            buf["name"] = tc_delta.function.name
                            yield StreamEvent(
                                type="tool_use_start",
                                tool_call=ToolCall(id=buf["id"], name=buf["name"], input={}),
                            )
                        if tc_delta.function.arguments:
                            buf["arguments"] += tc_delta.function.arguments
                            yield StreamEvent(type="tool_use_input", text=tc_delta.function.arguments)

            # Finish reason — remember it but don't emit done yet;
            # usage data may arrive in a subsequent chunk.
            if choice.finish_reason:
                finish_reason = choice.finish_reason
                # Emit completed tool calls
                for buf in tool_calls_buf.values():
                    try:
                        parsed = json.loads(buf["arguments"]) if buf["arguments"] else {}
                    except json.JSONDecodeError:
                        parsed = {}
                    yield StreamEvent(
                        type="tool_use_end",
                        tool_call=ToolCall(id=buf["id"], name=buf["name"], input=parsed),
                    )
                tool_calls_buf.clear()

        # Emit done AFTER the stream ends so usage is captured
        yield StreamEvent(
            type="done",
            stop_reason=finish_reason or "stop",
            usage=total_usage,
        )

    @staticmethod
    def _build_messages(messages: list[Message], system: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if system:
            result.append({"role": "system", "content": system})
        for m in messages:
            converted = m.to_openai()
            if isinstance(converted, list):
                result.extend(converted)
            else:
                result.append(converted)
        return result

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for t in tools
        ]
