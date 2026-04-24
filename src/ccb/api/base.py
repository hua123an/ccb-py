"""Provider interface and shared types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # Image attachments (multimodal). Each entry is an ImageContent from
    # ccb.images — stored as dicts here to avoid circular imports.
    images: list[dict[str, Any]] = field(default_factory=list)
    # File attachments (text content inlined in the prompt)
    files: list[dict[str, Any]] = field(default_factory=list)

    def to_anthropic(self) -> dict[str, Any]:
        if self.tool_results:
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tr.tool_use_id,
                        "content": tr.content,
                        **({"is_error": True} if tr.is_error else {}),
                    }
                    for tr in self.tool_results
                ],
            }
        if self.tool_calls:
            blocks: list[dict] = []
            if self.content:
                blocks.append({"type": "text", "text": self.content})
            for tc in self.tool_calls:
                blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                })
            return {"role": "assistant", "content": blocks}
        # Multimodal: images + optional file content + text
        if self.images or self.files:
            blocks: list[dict] = []
            for img in self.images:
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.get("media_type", "image/png"),
                        "data": img["base64_data"],
                    },
                })
            for fc in self.files:
                fname = fc.get("filename", "file")
                fcontent = fc.get("content", "")
                blocks.append({
                    "type": "text",
                    "text": f"<file name=\"{fname}\">\n{fcontent}\n</file>",
                })
            if self.content:
                blocks.append({"type": "text", "text": self.content})
            return {"role": self.role.value, "content": blocks}
        return {"role": self.role.value, "content": self.content}

    def to_openai(self, use_anthropic_images: bool = False) -> dict[str, Any]:
        """Convert to OpenAI chat format.

        Args:
            use_anthropic_images: When True, encode images in Anthropic's native
                format (``type: image``) instead of OpenAI's ``image_url``.  Many
                sub2api relays that proxy Claude models pass content blocks
                straight through to the Anthropic API, so native format ensures
                images actually reach the model.
        """
        import json as _json

        if self.tool_results:
            # OpenAI: each tool result is a separate message
            return [
                {
                    "role": "tool",
                    "tool_call_id": tr.tool_use_id,
                    "content": tr.content,
                }
                for tr in self.tool_results
            ]
        if self.tool_calls:
            msg: dict[str, Any] = {"role": "assistant", "content": self.content or None}
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": _json.dumps(tc.input)},
                }
                for tc in self.tool_calls
            ]
            return msg
        # Multimodal: images + file content + text
        if self.images or self.files:
            parts: list[dict] = []
            for img in self.images:
                mt = img.get("media_type", "image/png")
                b64 = img["base64_data"]
                if use_anthropic_images:
                    # Anthropic-native format — sub2api relays pass this
                    # straight through to the Anthropic API.
                    parts.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mt,
                            "data": b64,
                        },
                    })
                else:
                    parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mt};base64,{b64}",
                        },
                    })
            for fc in self.files:
                fname = fc.get("filename", "file")
                fcontent = fc.get("content", "")
                parts.append({
                    "type": "text",
                    "text": f"<file name=\"{fname}\">\n{fcontent}\n</file>",
                })
            if self.content:
                parts.append({"type": "text", "text": self.content})
            return {"role": self.role.value, "content": parts}
        return {"role": self.role.value, "content": self.content}


@dataclass
class StreamEvent:
    """Unified streaming event."""
    type: str  # "text", "thinking", "tool_use_start", "tool_use_input", "tool_use_end", "done", "error"
    text: str = ""
    tool_call: ToolCall | None = None
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None


class Provider(ABC):
    """Abstract API provider."""

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        system: str = "",
        max_tokens: int = 16384,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a response, yielding StreamEvents."""
        ...

    @abstractmethod
    def name(self) -> str: ...

    def set_model(self, model: str) -> None:
        """Change the model used by this provider."""
        if hasattr(self, "_model"):
            self._model = model

    def set_thinking(self, enabled: bool, budget: int = 10000, mode: str = "") -> None:
        """Configure thinking/reasoning mode. Override in subclasses."""
        pass

    @property
    def supports_thinking(self) -> bool:
        """Whether this provider supports extended thinking."""
        return False
