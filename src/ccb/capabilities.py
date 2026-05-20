from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelCapabilities:
    supports_tools: bool = True
    supports_thinking: bool = False
    supports_prefill: bool = False
    supports_images: bool = False
    supports_vision: bool = False
    supports_parallel_tool_calls: bool = False
    supports_system_prompt: bool = True
    max_context_tokens: int = 128_000
    recommended_max_tokens: int = 4096
    recommended_temperature: float = 1.0


# Provider-level defaults
_PROVIDER_DEFAULTS: dict[str, ModelCapabilities] = {
    "anthropic": ModelCapabilities(
        supports_tools=True,
        supports_thinking=True,
        supports_prefill=True,
        supports_images=True,
        supports_vision=True,
        supports_parallel_tool_calls=True,
        supports_system_prompt=True,
        max_context_tokens=200_000,
        recommended_max_tokens=8192,
        recommended_temperature=1.0,
    ),
    "openai": ModelCapabilities(
        supports_tools=True,
        supports_thinking=False,
        supports_prefill=True,  # handled via trailing assistant message injection
        supports_images=True,
        supports_vision=True,
        supports_parallel_tool_calls=True,
        supports_system_prompt=True,
        max_context_tokens=128_000,
        recommended_max_tokens=4096,
        recommended_temperature=1.0,
    ),
    "gemini": ModelCapabilities(
        supports_tools=True,
        supports_thinking=False,
        supports_prefill=True,  # handled via trailing model message injection
        supports_images=True,
        supports_vision=True,
        supports_parallel_tool_calls=False,
        supports_system_prompt=True,
        max_context_tokens=1_000_000,
        recommended_max_tokens=8192,
        recommended_temperature=1.0,
    ),
    "bedrock": ModelCapabilities(
        supports_tools=True,
        supports_thinking=False,
        supports_prefill=True,  # handled via trailing assistant message injection
        supports_images=True,
        supports_vision=True,
        supports_parallel_tool_calls=True,
        supports_system_prompt=True,
        max_context_tokens=200_000,
        recommended_max_tokens=4096,
        recommended_temperature=1.0,
    ),
    "vertex": ModelCapabilities(
        supports_tools=True,
        supports_thinking=True,
        supports_prefill=True,  # handled via trailing model message injection
        supports_images=True,
        supports_vision=True,
        supports_parallel_tool_calls=True,
        supports_system_prompt=True,
        max_context_tokens=200_000,
        recommended_max_tokens=8192,
        recommended_temperature=1.0,
    ),
    "grok": ModelCapabilities(
        supports_tools=False,
        supports_thinking=False,
        supports_prefill=True,  # handled via trailing assistant message injection
        supports_images=True,
        supports_vision=False,
        supports_parallel_tool_calls=False,
        supports_system_prompt=True,
        max_context_tokens=128_000,
        recommended_max_tokens=4096,
        recommended_temperature=1.0,
    ),
}

# Model-specific overrides
_MODEL_OVERRIDES: dict[str, ModelCapabilities] = {
    "claude-3-5-sonnet": ModelCapabilities(
        supports_tools=True, supports_thinking=True, supports_prefill=True,
        supports_images=True, supports_vision=True, supports_parallel_tool_calls=True,
        max_context_tokens=200_000, recommended_max_tokens=8192, recommended_temperature=1.0,
    ),
    "claude-3-7-sonnet": ModelCapabilities(
        supports_tools=True, supports_thinking=True, supports_prefill=True,
        supports_images=True, supports_vision=True, supports_parallel_tool_calls=True,
        max_context_tokens=200_000, recommended_max_tokens=8192, recommended_temperature=1.0,
    ),
    "claude-3-opus": ModelCapabilities(
        supports_tools=True, supports_thinking=False, supports_prefill=True,
        supports_images=True, supports_vision=True, supports_parallel_tool_calls=True,
        max_context_tokens=200_000, recommended_max_tokens=4096, recommended_temperature=1.0,
    ),
    "claude-3-haiku": ModelCapabilities(
        supports_tools=True, supports_thinking=False, supports_prefill=True,
        supports_images=True, supports_vision=True, supports_parallel_tool_calls=True,
        max_context_tokens=200_000, recommended_max_tokens=4096, recommended_temperature=1.0,
    ),
    "gpt-4o": ModelCapabilities(
        supports_tools=True, supports_thinking=False, supports_prefill=False,
        supports_images=True, supports_vision=True, supports_parallel_tool_calls=True,
        max_context_tokens=128_000, recommended_max_tokens=4096, recommended_temperature=0.7,
    ),
    "gpt-4o-mini": ModelCapabilities(
        supports_tools=True, supports_thinking=False, supports_prefill=False,
        supports_images=True, supports_vision=True, supports_parallel_tool_calls=True,
        max_context_tokens=128_000, recommended_max_tokens=4096, recommended_temperature=0.7,
    ),
    "o1": ModelCapabilities(
        supports_tools=True, supports_thinking=True, supports_prefill=False,
        supports_images=False, supports_vision=False, supports_parallel_tool_calls=True,
        max_context_tokens=200_000, recommended_max_tokens=8192, recommended_temperature=1.0,
    ),
    "o3-mini": ModelCapabilities(
        supports_tools=True, supports_thinking=True, supports_prefill=False,
        supports_images=False, supports_vision=False, supports_parallel_tool_calls=True,
        max_context_tokens=200_000, recommended_max_tokens=8192, recommended_temperature=1.0,
    ),
    "gemini-1.5-pro": ModelCapabilities(
        supports_tools=True, supports_thinking=False, supports_prefill=False,
        supports_images=True, supports_vision=True, supports_parallel_tool_calls=False,
        max_context_tokens=1_000_000, recommended_max_tokens=8192, recommended_temperature=1.0,
    ),
    "gemini-1.5-flash": ModelCapabilities(
        supports_tools=True, supports_thinking=False, supports_prefill=False,
        supports_images=True, supports_vision=True, supports_parallel_tool_calls=False,
        max_context_tokens=1_000_000, recommended_max_tokens=4096, recommended_temperature=1.0,
    ),
    "gemini-2.0-flash": ModelCapabilities(
        supports_tools=True, supports_thinking=False, supports_prefill=False,
        supports_images=True, supports_vision=True, supports_parallel_tool_calls=False,
        max_context_tokens=1_000_000, recommended_max_tokens=8192, recommended_temperature=1.0,
    ),
}


def _model_slug(model: str) -> str:
    """Canonicalize model name for lookup."""
    m = model.lower().strip()
    # Remove version suffixes like -20241022, -latest, etc.
    for suffix in ("-latest", "-2024", "-2025", "-v1", "-001", "-002"):
        if m.endswith(suffix):
            m = m[: -len(suffix)]
    # Remove date suffixes like -20241022
    if len(m) > 4 and m[-8:].isdigit():
        m = m[:-8]
    return m


def get_capabilities(provider_name: str, model: str) -> ModelCapabilities:
    """Get capabilities for a provider + model combination.

    Falls back through:
    1. Exact model override lookup
    2. Provider default
    3. Conservative universal default
    """
    slug = _model_slug(model)

    # Try model-specific override
    for key, caps in _MODEL_OVERRIDES.items():
        if key in slug:
            return caps

    # Try provider default
    provider = provider_name.lower().strip()
    if provider in _PROVIDER_DEFAULTS:
        return _PROVIDER_DEFAULTS[provider]

    # Ultra-conservative fallback
    return ModelCapabilities(
        supports_tools=False,
        supports_thinking=False,
        supports_prefill=False,
        supports_images=False,
        supports_vision=False,
        supports_parallel_tool_calls=False,
        supports_system_prompt=True,
        max_context_tokens=128_000,
        recommended_max_tokens=4096,
        recommended_temperature=1.0,
    )


def infer_provider_from_model(model: str) -> str:
    """Infer likely provider from model name."""
    m = model.lower()
    if "claude" in m:
        return "anthropic"
    if "gpt" in m or "o1" in m or "o3" in m:
        return "openai"
    if "gemini" in m:
        return "gemini"
    if "grok" in m:
        return "grok"
    return "openai"  # safest default for unknown models


def adapt_params_for_capabilities(
    caps: ModelCapabilities,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    tools: list[dict[str, Any]] | None = None,
    system: str = "",
    prefill: str = "",
    images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return sanitized parameters that respect the model's capabilities.

    This is the **model-agnostic quality gate**: before every call,
    use this to strip parameters the model cannot handle, avoiding
    400/BadRequest errors from provider APIs.
    """
    result: dict[str, Any] = {}

    # max_tokens
    if max_tokens is not None:
        result["max_tokens"] = min(max_tokens, caps.recommended_max_tokens)
    else:
        result["max_tokens"] = caps.recommended_max_tokens

    # temperature — some reasoning models ignore this, but it's safe to send
    result["temperature"] = temperature if temperature is not None else caps.recommended_temperature

    # tools
    if tools and caps.supports_tools:
        result["tools"] = tools
        if not caps.supports_parallel_tool_calls and len(tools) > 1:
            if system:
                system += "\n\n"
            system += (
                "When using tools, call at most one tool at a time. "
                "Wait for the tool result before deciding whether another tool is needed."
            )
        result["system"] = system
    elif tools and not caps.supports_tools:
        # Model doesn't support tools: inject them into system prompt instead
        if system:
            system += "\n\n"
        tool_text = "\n".join(
            f"- {t.get('name', 'tool')}: {t.get('description', '')}"
            for t in tools
        )
        system += f"You have access to these tools:\n{tool_text}\n"
        result["system"] = system
    elif not caps.supports_system_prompt and system:
        # Model doesn't support system prompts: caller should inject as first user message
        result["system"] = ""
        result["system_fallback_needed"] = system
    else:
        result["system"] = system

    # prefill
    if prefill and caps.supports_prefill:
        result["prefill"] = prefill
    elif prefill and not caps.supports_prefill:
        # Can't prefill: silently drop — caller will handle via message injection
        pass

    # images
    if images and not (caps.supports_images or caps.supports_vision):
        result["images_dropped"] = len(images)

    return result
