"""AWS Bedrock provider for Claude models via Bedrock."""
from __future__ import annotations

import json
import logging
import os
import time
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


class BedrockProvider(Provider):
    """AWS Bedrock provider using boto3 + anthropic Bedrock runtime."""

    def __init__(self, model: str, base_url: str | None = None, region: str = "us-east-1"):
        self._model = model
        self._region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._thinking_enabled = False
        self._thinking_budget = 10000
        self._thinking_mode = "off"

        # Initialize boto3 client for Bedrock Runtime
        try:
            import boto3
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self._region,
            )
            self._has_boto3 = True
        except ImportError:
            self._client = None
            self._has_boto3 = False

    def name(self) -> str:
        return "bedrock"

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
        if not self._has_boto3:
            yield StreamEvent(
                type="error",
                error="boto3 not installed. Install with: pip install boto3",
            )
            return

        # Convert messages to Bedrock format
        api_messages = [self._msg_to_bedrock(m) for m in messages]
        if prefill:
            api_messages.append({"role": "assistant", "content": [{"type": "text", "text": prefill}]})

        # Build request payload
        payload: dict[str, Any] = {
            "modelId": self._model,
            "messages": api_messages,
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": 1.0 if temperature is None else temperature,
            },
        }

        if system:
            payload["system"] = [{"text": system}]

        if tools:
            payload["tools"] = self._convert_tools(tools)

        # Extended thinking for Bedrock
        if self._thinking_mode == "adaptive":
            payload["inferenceConfig"]["thinking"] = {
                "type": "adaptive",
                "budgetTokens": self._thinking_budget,
            }
        elif self._thinking_enabled:
            payload["inferenceConfig"]["thinking"] = {
                "type": "enabled",
                "budgetTokens": self._thinking_budget,
            }

        # Echo prefill
        if prefill:
            yield StreamEvent(type="text", text=prefill)

        # Retry loop for transient errors
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = self._client.invoke_model_with_response_stream(
                    modelId=self._model,
                    body=json.dumps(payload),
                    accept="application/json",
                    contentType="application/json",
            )
                break  # success
            except Exception as e:
                if attempt < _MAX_RETRIES:
                    delay = retry_delay(attempt)
                    logger.debug("Bedrock error (attempt %d/%d), retrying in %.1fs", attempt + 1, _MAX_RETRIES, delay)
                    time.sleep(delay)
                    continue
                yield StreamEvent(type="error", error=f"Bedrock error after {_MAX_RETRIES} retries: {e}")
                return

        # Stream response (no retry needed for streaming itself)
        current_tool: dict[str, Any] | None = None
        input_json_buf = ""
        text_buf = ""

        for event in response.get("body"):
            chunk = json.loads(event["chunk"]["bytes"])

            # Handle different event types
            if chunk.get("type") == "content_block_start":
                block = chunk.get("contentBlock", {})
                if block.get("type") == "tool_use":
                    current_tool = {"id": block.get("id"), "name": block.get("name")}
                    input_json_buf = ""
                    yield StreamEvent(
                        type="tool_use_start",
                        tool_call=ToolCall(id=block.get("id", ""), name=block.get("name", ""), input={}),
                    )

            elif chunk.get("type") == "content_block_delta":
                delta = chunk.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    text_buf += text
                    yield StreamEvent(type="text", text=text)
                elif delta.get("type") == "thinking_delta":
                    yield StreamEvent(type="thinking", text=delta.get("thinking", ""))
                elif delta.get("type") == "input_json_delta":
                    input_json_buf += delta.get("partialJson", "")
                    yield StreamEvent(type="tool_use_input", text=delta.get("partialJson", ""))

            elif chunk.get("type") == "content_block_stop":
                if current_tool:
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

            elif chunk.get("type") == "message_stop":
                usage = chunk.get("usage", {})
                yield StreamEvent(
                    type="done",
                    usage={
                        "input_tokens": usage.get("inputTokens", 0),
                        "output_tokens": usage.get("outputTokens", 0),
                    },
                )

    @staticmethod
    def _msg_to_bedrock(msg: Message) -> dict[str, Any]:
        """Convert Message to Bedrock format."""
        parts: list[dict[str, Any]] = []
        if msg.content:
            parts.append({"type": "text", "text": msg.content})
        if msg.tool_calls:
            for tc in msg.tool_calls:
                parts.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input or {},
                })
        if msg.tool_results:
            for tr in msg.tool_results:
                parts.append({
                    "type": "tool_result",
                    "tool_use_id": tr.tool_use_id,
                    "content": tr.content,
                })
        return {"role": msg.role.value, "content": parts}

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert tools to Bedrock format."""
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "inputSchema": t.get("input_schema", {"type": "object", "properties": {}}),
            }
            for t in tools
        ]
