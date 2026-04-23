"""MCP Sampling support.

Implements the MCP sampling spec — allows MCP servers to request
LLM completions from the client. The server sends a sampling/createMessage
request, the client runs it through its LLM and returns the result.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class SamplingRequest:
    """A sampling request from an MCP server."""
    messages: list[dict[str, Any]]
    model_preferences: dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""
    max_tokens: int = 4096
    temperature: float | None = None
    stop_sequences: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    include_context: str = "none"  # "none", "thisServer", "allServers"


@dataclass
class SamplingResponse:
    """Response to a sampling request."""
    role: str = "assistant"
    content: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    stop_reason: str = ""


# Type for the completion callback
CompletionCallback = Callable[[SamplingRequest], Awaitable[SamplingResponse]]


class SamplingHandler:
    """Handles MCP sampling requests from servers.

    Bridges MCP sampling requests to the ccb-py provider.
    """

    def __init__(self) -> None:
        self._callback: CompletionCallback | None = None
        self._enabled = True
        self._requests_handled = 0
        self._max_tokens_limit = 8192  # Safety limit
        self._allowed_models: list[str] = []  # Empty = allow all
        self._require_approval = False

    def set_callback(self, callback: CompletionCallback) -> None:
        """Set the LLM completion callback."""
        self._callback = callback

    def set_max_tokens_limit(self, limit: int) -> None:
        self._max_tokens_limit = limit

    def set_require_approval(self, require: bool) -> None:
        self._require_approval = require

    def allow_model(self, model: str) -> None:
        self._allowed_models.append(model)

    @property
    def enabled(self) -> bool:
        return self._enabled and self._callback is not None

    def toggle(self) -> bool:
        self._enabled = not self._enabled
        return self._enabled

    async def handle_request(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle a sampling/createMessage request from MCP server.

        Called by the MCP client when it receives a sampling request.
        """
        if not self._callback:
            return {"error": {"code": -32603, "message": "Sampling not configured"}}

        if not self._enabled:
            return {"error": {"code": -32603, "message": "Sampling disabled"}}

        try:
            request = self._parse_request(params)

            # Safety checks
            if request.max_tokens > self._max_tokens_limit:
                request.max_tokens = self._max_tokens_limit

            if self._allowed_models and request.model_preferences:
                # Could filter models here
                pass

            if self._require_approval:
                # In a real impl, this would prompt the user
                pass

            response = await self._callback(request)
            self._requests_handled += 1

            return {
                "role": response.role,
                "content": response.content,
                "model": response.model,
                "stopReason": response.stop_reason or "end_turn",
            }

        except Exception as e:
            return {"error": {"code": -32603, "message": str(e)}}

    def _parse_request(self, params: dict[str, Any]) -> SamplingRequest:
        messages = params.get("messages", [])
        return SamplingRequest(
            messages=messages,
            model_preferences=params.get("modelPreferences", {}),
            system_prompt=params.get("systemPrompt", ""),
            max_tokens=params.get("maxTokens", 4096),
            temperature=params.get("temperature"),
            stop_sequences=params.get("stopSequences", []),
            metadata=params.get("metadata", {}),
            include_context=params.get("includeContext", "none"),
        )

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "configured": self._callback is not None,
            "requests_handled": self._requests_handled,
            "max_tokens_limit": self._max_tokens_limit,
            "require_approval": self._require_approval,
        }


def create_provider_sampling_callback(provider: Any) -> CompletionCallback:
    """Create a sampling callback that uses a ccb-py provider.

    This bridges MCP sampling → ccb-py provider API.
    """
    async def callback(request: SamplingRequest) -> SamplingResponse:
        from ccb.api.base import Message, Role

        # Convert MCP messages to ccb-py messages
        messages = []
        for m in request.messages:
            role = Role.USER if m.get("role") == "user" else Role.ASSISTANT
            content = m.get("content", {})
            if isinstance(content, dict):
                text = content.get("text", "")
            elif isinstance(content, str):
                text = content
            else:
                text = str(content)
            messages.append(Message(role=role, content=text))

        # Call the provider
        system = request.system_prompt or ""
        result_text = ""
        async for event in provider.stream(
            messages=messages,
            tools=[],
            system=system,
            max_tokens=request.max_tokens,
        ):
            if event.type == "text":
                result_text += event.text

        return SamplingResponse(
            role="assistant",
            content={"type": "text", "text": result_text},
            model=getattr(provider, "model", "unknown"),
            stop_reason="end_turn",
        )

    return callback
