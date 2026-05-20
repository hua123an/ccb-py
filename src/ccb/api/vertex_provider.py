"""Google Cloud Vertex AI provider for Claude models."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, AsyncIterator

from ccb.api.base import (
    _MAX_RETRIES,
    Message,
    Provider,
    retry_delay,
    StreamEvent,
    ToolCall,
)

logger = logging.getLogger(__name__)


class VertexProvider(Provider):
    """Google Cloud Vertex AI provider using google-auth + Anthropic on Vertex."""

    def __init__(self, model: str, base_url: str | None = None, region: str = "us-central1"):
        self._model = model
        self._region = region or os.environ.get("GCP_REGION", "us-central1")
        self._project_id = os.environ.get("GCP_PROJECT", "")
        self._thinking_enabled = False
        self._thinking_budget = 10000
        self._thinking_mode = "off"

        # Initialize Vertex AI client
        try:
            from google import genai
            from google.genai import types

            # Configure client for Vertex AI
            self._genai = genai
            self._types = types
            self._client_config = {
                "region": self._region,
                "project": self._project_id,
            }
            self._has_vertex = True
        except ImportError:
            self._genai = None
            self._types = None
            self._has_vertex = False

    def name(self) -> str:
        return "vertex"

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
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if not self._has_vertex:
            yield StreamEvent(
                type="error",
                error="google-genai not installed. Install with: pip install google-genai",
            )
            return

        if not self._project_id:
            yield StreamEvent(
                type="error",
                error="GCP_PROJECT environment variable not set",
            )
            return

        # Convert messages to Vertex format
        api_messages = [self._msg_to_vertex(m) for m in messages]
        if prefill:
            api_messages.append(self._types.Content(
                role="model",
                parts=[self._types.Part(text=prefill)],
            ))

        # Build config
        config = self._types.GenerateContentConfig(
            system_instruction=[self._types.Part(text=system)] if system else None,
            tools=self._convert_tools(tools) if tools else None,
            thinking_config=self._types.ThinkingConfig(
                include_thoughts=self._thinking_enabled,
                thinking_budget=self._thinking_budget if self._thinking_enabled else 0,
            ) if self._thinking_enabled or self._thinking_mode == "adaptive" else None,
            max_output_tokens=max_tokens,
            temperature=1.0 if temperature is None else temperature,
        )

        # Echo prefill
        if prefill:
            yield StreamEvent(type="text", text=prefill)

        # Retry loop for transient errors
        for attempt in range(_MAX_RETRIES + 1):
            try:
                model_name = f"projects/{self._project_id}/locations/{self._region}/publishers/anthropic/models/{self._model}"

                async def generate():
                    client = self._genai.Client(
                        vertexai=True,
                        project=self._project_id,
                        location=self._region,
                    )
                    return await client.aio.models.generate_content_stream(
                        model=model_name,
                        contents=api_messages,
                        config=config,
                    )

                response = await asyncio.wait_for(generate(), timeout=180)
                break  # success
            except Exception as e:
                if attempt < _MAX_RETRIES:
                    delay = retry_delay(attempt)
                    logger.debug("Vertex AI error (attempt %d/%d), retrying in %.1fs", attempt + 1, _MAX_RETRIES, delay)
                    await asyncio.sleep(delay)
                    continue
                yield StreamEvent(type="error", error=f"Vertex AI error after {_MAX_RETRIES} retries: {e}")
                return

        # Stream response (no retry needed for streaming itself)
        current_tool: dict[str, Any] | None = None

        async for chunk in response:
            # Handle text
            for part in chunk.candidates[0].content.parts:
                if part.text:
                    yield StreamEvent(type="text", text=part.text)

                # Handle tool use
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

            # Handle tool result
            if current_tool and chunk.candidates[0].content.parts:
                for part in chunk.candidates[0].content.parts:
                    if hasattr(part, "function_response"):
                        assert current_tool is not None
                        import json
                        try:
                            parsed_input = json.loads(current_tool["args"]) if current_tool["args"] else {}
                        except json.JSONDecodeError:
                            parsed_input = {}
                        tc = ToolCall(
                            id=current_tool["id"],
                            name=current_tool["name"],
                            input=parsed_input,
                        )
                        yield StreamEvent(type="tool_use_end", tool_call=tc)
                        current_tool = None

            # Check for usage
            if hasattr(chunk, "usage_metadata"):
                yield StreamEvent(
                    type="done",
                    usage={
                        "input_tokens": getattr(chunk.usage_metadata, "prompt_token_count", 0),
                        "output_tokens": getattr(chunk.usage_metadata, "candidates_token_count", 0),
                    },
                )

        # If no usage metadata, emit done at end
        yield StreamEvent(type="done", usage={"input_tokens": 0, "output_tokens": 0})

    @staticmethod
    def _msg_to_vertex(msg: Message) -> Any:
        """Convert Message to Vertex format."""
        from google.genai import types

        parts = []
        if msg.content:
            parts.append(types.Part(text=msg.content))
        if msg.tool_calls:
            for tc in msg.tool_calls:
                parts.append(types.Part(
                    function_call=types.FunctionCall(
                        id=tc.id,
                        name=tc.name,
                        args=tc.input,
                    )
                ))
        if msg.tool_results:
            for tr in msg.tool_results:
                parts.append(types.Part(
                    function_response=types.FunctionResponse(
                        id=tr.tool_use_id,
                        name=tr.tool_use_id,  # Vertex uses name
                        response={"result": tr.content},
                    )
                ))

        return types.Content(role=msg.role.value, parts=parts)

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[Any]:
        """Convert tools to Vertex format."""
        from google.genai import types

        result = []
        for t in tools:
            result.append(
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t["name"],
                            description=t.get("description", ""),
                            parameters=t.get("input_schema", {"type": "object", "properties": {}}),
                        )
                    ]
                )
            )
        return result
