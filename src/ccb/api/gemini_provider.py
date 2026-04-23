"""Google Gemini provider - uses OpenAI-compatible endpoint."""
from __future__ import annotations

from typing import Any

from ccb.api.openai_provider import OpenAIProvider


class GeminiProvider(OpenAIProvider):
    """Gemini via Google's OpenAI-compatible API or custom base URL."""

    GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url or self.GEMINI_BASE_URL,
        )

    def name(self) -> str:
        return "gemini"
