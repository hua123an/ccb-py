"""Google Gemini provider — native API with thinking, tools, streaming.

Uses google-genai SDK when available, falls back to OpenAI-compatible endpoint.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from ccb.api.base import Message, Provider, Role, StreamEvent, ToolCall


class GeminiProvider(Provider):
    """Google Gemini provider with native API support."""

    GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url or self.GEMINI_BASE_URL
        self._thinking_enabled = False
        self._thinking_budget = 10000
        self._thinking_mode = "off"

        # Try native SDK first
        try:
            from google import genai
            self._genai = genai
            self._client = genai.Client(api_key=api_key)
            self._has_native = True
        except ImportError:
            self._genai = None
            self._client = None
            self._has_native = False

    def name(self) -> str:
        return "gemini"

    @property
    def supports_thinking(self) -> bool:
        return True

    def set_thinking(self, enabled: bool, budget: int = 10000, mode: str = "") -> None:
        self._thinking_enabled = enabled
        self._thinking_budget = max(1024, budget)
        if mode:
            self._thinking_mode = mode
        else:
            self._thinking_mode = "on" if enabled else "off"

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        system: str = "",
        max_tokens: int = 16384,
        prefill: str = "",
    ) -> AsyncIterator[StreamEvent]:
        if self._has_native:
            async for event in self._stream_native(messages, tools, system, max_tokens, prefill):
                yield event
        else:
            async for event in self._stream_openai_compat(messages, tools, system, max_tokens, prefill):
                yield event

    async def _stream_native(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        prefill: str,
    ) -> AsyncIterator[StreamEvent]:
        """Stream using native google-genai SDK."""
        from google.genai import types

        # Convert messages
        contents = self._messages_to_gemini(messages)
        if prefill:
            contents.append(types.Content(
                role="model",
                parts=[types.Part(text=prefill)],
            ))

        # Build config
        config = types.GenerateContentConfig(
            system_instruction=[types.Part(text=system)] if system else None,
            tools=self._convert_tools(tools) if tools else None,
            thinking_config=types.ThinkingConfig(
                include_thoughts=self._thinking_enabled,
                thinking_budget=self._thinking_budget if self._thinking_enabled else 0,
            ) if self._thinking_enabled or self._thinking_mode == "adaptive" else None,
            max_output_tokens=max_tokens,
            temperature=1.0,
        )

        # Echo prefill
        if prefill:
            yield StreamEvent(type="text", text=prefill)

        try:
            import asyncio

            async def generate():
                return await self._client.aio.models.generate_content_stream(
                    model=self._model,
                    contents=contents,
                    config=config,
                )

            response = await asyncio.wait_for(generate(), timeout=180)

            current_tool: dict[str, Any] | None = None

            async for chunk in response:
                if not chunk.candidates:
                    continue

                for part in chunk.candidates[0].content.parts:
                    # Handle thinking
                    if hasattr(part, "thought") and part.thought and part.text:
                        yield StreamEvent(type="thinking", text=part.text)
                        continue

                    # Handle text
                    if part.text:
                        yield StreamEvent(type="text", text=part.text)

                    # Handle tool calls
                    if hasattr(part, "function_call") and part.function_call:
                        fc = part.function_call
                        if not current_tool:
                            current_tool = {"id": fc.id or "", "name": fc.name, "args": ""}
                            yield StreamEvent(
                                type="tool_use_start",
                                tool_call=ToolCall(id=fc.id or "", name=fc.name, input={}),
                            )
                        if fc.args:
                            current_tool["args"] += fc.args
                            yield StreamEvent(type="tool_use_input", text=fc.args)

                # Check if tool call is complete
                if current_tool and chunk.candidates[0].content.parts:
                    for part in chunk.candidates[0].content.parts:
                        if hasattr(part, "function_call") and part.function_call:
                            import json
                            try:
                                parsed = json.loads(current_tool["args"]) if current_tool["args"] else {}
                            except json.JSONDecodeError:
                                parsed = {}
                            tc = ToolCall(
                                id=current_tool["id"],
                                name=current_tool["name"],
                                input=parsed,
                            )
                            yield StreamEvent(type="tool_use_end", tool_call=tc)
                            current_tool = None

                # Usage
                if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                    yield StreamEvent(
                        type="done",
                        usage={
                            "input_tokens": getattr(chunk.usage_metadata, "prompt_token_count", 0),
                            "output_tokens": getattr(chunk.usage_metadata, "candidates_token_count", 0),
                        },
                    )

            yield StreamEvent(type="done", usage={"input_tokens": 0, "output_tokens": 0})

        except Exception as e:
            yield StreamEvent(type="error", error=f"Gemini error: {e}")

    async def _stream_openai_compat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        prefill: str,
    ) -> AsyncIterator[StreamEvent]:
        """Fallback: stream via OpenAI-compatible endpoint."""
        import httpx
        import json

        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})

        for msg in messages:
            if msg.role == Role.USER:
                api_messages.append({"role": "user", "content": msg.content})
            elif msg.role == Role.ASSISTANT:
                m: dict[str, Any] = {"role": "assistant"}
                if msg.content:
                    m["content"] = msg.content
                if msg.tool_calls:
                    m["tool_calls"] = [
                        {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.input)}}
                        for tc in msg.tool_calls
                    ]
                api_messages.append(m)
            elif msg.role == Role.TOOL_RESULT:
                for tr in msg.tool_results:
                    api_messages.append({"role": "tool", "tool_call_id": tr.tool_use_id, "content": tr.content})

        if prefill:
            api_messages.append({"role": "assistant", "content": prefill})

        api_tools = None
        if tools:
            api_tools = [
                {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema", {})}}
                for t in tools
            ]

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if api_tools:
            payload["tools"] = api_tools

        if prefill:
            yield StreamEvent(type="text", text=prefill)

        try:
            async with httpx.AsyncClient(timeout=180) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        yield StreamEvent(type="error", error=f"Gemini API error {resp.status_code}: {body.decode()[:500]}")
                        return

                    current_tool: dict[str, Any] | None = None
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        delta = chunk.get("choices", [{}])[0].get("delta", "")

                        if isinstance(delta, dict):
                            if delta.get("content"):
                                yield StreamEvent(type="text", text=delta["content"])

                            if delta.get("tool_calls"):
                                for tc_delta in delta["tool_calls"]:
                                    fn = tc_delta.get("function", {})
                                    if tc_delta.get("id"):
                                        current_tool = {"id": tc_delta["id"], "name": fn.get("name", ""), "args": ""}
                                        yield StreamEvent(
                                            type="tool_use_start",
                                            tool_call=ToolCall(id=tc_delta["id"], name=fn.get("name", ""), input={}),
                                        )
                                    if fn.get("arguments") and current_tool:
                                        current_tool["args"] += fn["arguments"]
                                        yield StreamEvent(type="tool_use_input", text=fn["arguments"])

                        finish = chunk.get("choices", [{}])[0].get("finish_reason")
                        if finish == "tool_calls" and current_tool:
                            try:
                                parsed = json.loads(current_tool["args"])
                            except json.JSONDecodeError:
                                parsed = {}
                            tc = ToolCall(id=current_tool["id"], name=current_tool["name"], input=parsed)
                            yield StreamEvent(type="tool_use_end", tool_call=tc)
                            current_tool = None

                        usage = chunk.get("usage")
                        if usage:
                            yield StreamEvent(
                                type="done",
                                usage={"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)},
                            )

            yield StreamEvent(type="done", usage={"input_tokens": 0, "output_tokens": 0})

        except Exception as e:
            yield StreamEvent(type="error", error=f"Gemini error: {e}")

    def _messages_to_gemini(self, messages: list[Message]) -> list[Any]:
        """Convert messages to Gemini native format."""
        from google.genai import types

        contents = []
        for msg in messages:
            parts = []
            if msg.content:
                parts.append(types.Part(text=msg.content))
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    parts.append(types.Part(
                        function_call=types.FunctionCall(id=tc.id, name=tc.name, args=tc.input)
                    ))
            if msg.tool_results:
                for tr in msg.tool_results:
                    parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            id=tr.tool_use_id, name=tr.tool_use_id,
                            response={"result": tr.content},
                        )
                    ))

            role = "user" if msg.role in (Role.USER, Role.TOOL_RESULT) else "model"
            contents.append(types.Content(role=role, parts=parts))

        return contents

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[Any]:
        """Convert tools to Gemini native format."""
        from google.genai import types

        return [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=t["name"],
                        description=t.get("description", ""),
                        parameters=t.get("input_schema", {"type": "object", "properties": {}}),
                    )
                ]
            )
            for t in tools
        ]
