"""OpenAI-compatible provider (GPT, Grok, local models, etc.)."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from ccb.api.base import Message, Provider, StreamEvent, ToolCall


def _is_reasoning_model(model: str) -> bool:
    """Check if a model supports reasoning_effort parameter."""
    m = model.lower()
    # o-series: o1, o1-mini, o1-pro, o3, o3-mini, o4-mini
    if any(m.startswith(p) for p in ("o1", "o3", "o4")):
        return True
    # gpt-5+: gpt-5, gpt-5.4, gpt-5.5, etc.
    if m.startswith("gpt-5"):
        return True
    return False


class OpenAIProvider(Provider):
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self._model = model
        self._reasoning_effort: str | None = None  # None = model default
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            # OpenAI SDK expects base_url to end with /v1
            bu = base_url.rstrip("/")
            if not bu.endswith("/v1"):
                bu += "/v1"
            kwargs["base_url"] = bu
        # Set explicit timeouts: 30s connect, 120s read (image uploads need time)
        kwargs["timeout"] = {"connect": 30, "read": 120, "write": 30, "pool": 5}
        self._client = AsyncOpenAI(**kwargs)

    def name(self) -> str:
        return "openai"

    @property
    def supports_thinking(self) -> bool:
        return _is_reasoning_model(self._model)

    def set_thinking(self, enabled: bool, budget: int = 10000, mode: str = "") -> None:
        """Map thinking settings to OpenAI reasoning_effort.

        Mapping:
          adaptive / on  → reasoning_effort "high"
          off            → None (model default)
        Budget is Anthropic-specific and ignored here.
        """
        if not enabled and mode != "adaptive":
            self._reasoning_effort = None
            return
        if mode == "adaptive":
            # Let model decide: use "medium" so it's not always maxing out
            self._reasoning_effort = "medium"
        else:
            self._reasoning_effort = "high"

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
        # Reasoning effort for o-series / gpt-5+ models
        if self._reasoning_effort and _is_reasoning_model(self._model):
            kwargs["reasoning_effort"] = self._reasoning_effort
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

    def _build_messages(self, messages: list[Message], system: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if system:
            result.append({"role": "system", "content": system})
        # When the model is Claude running behind an OpenAI-compatible relay
        # (sub2api, windsurf, etc.), use Anthropic-native image format so the
        # relay can pass image blocks straight through to the Anthropic API.
        from ccb.api.router import _is_claude_model
        use_anthro_img = _is_claude_model(self._model)
        for m in messages:
            converted = m.to_openai(use_anthropic_images=use_anthro_img)
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
