"""ImageGenerationTool - generate images via AI APIs."""
from __future__ import annotations

import os
from typing import Any

from ccb.tools.base import Tool, ToolResult

IMAGE_GEN_PROMPT = """\
Generate images using AI image generation APIs.

Usage:
- Provide a text description (prompt) of the image to generate
- Images are saved to the specified output directory (default: current working directory)
- Supports DALL-E 3 (via OpenAI API) and other compatible endpoints
- Returns the saved file path and generation details
- Requires OPENAI_API_KEY or compatible API key

Tips:
- Be specific and detailed in your prompt for best results
- Specify style, composition, lighting, and mood
- Supported sizes: 1024x1024, 1024x1792, 1792x1024 (DALL-E 3)
- Supported qualities: standard, hd"""


class ImageGenerationTool(Tool):
    name = "image_gen"
    description = IMAGE_GEN_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Text description of the image to generate.",
            },
            "size": {
                "type": "string",
                "description": "Image size: 1024x1024, 1024x1792, or 1792x1024 (default: 1024x1024).",
                "default": "1024x1024",
            },
            "quality": {
                "type": "string",
                "description": "Image quality: standard or hd (default: standard).",
                "default": "standard",
            },
            "filename": {
                "type": "string",
                "description": "Output filename (without extension). Auto-generated if not provided.",
            },
        },
        "required": ["prompt"],
    }

    @property
    def needs_permission(self) -> bool:
        return True

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        prompt = input.get("prompt", "")
        size = input.get("size", "1024x1024")
        quality = input.get("quality", "standard")
        filename = input.get("filename", "")

        if not prompt.strip():
            return ToolResult(output="Error: empty prompt", is_error=True)

        # Determine API key and base URL
        api_key = os.environ.get("OPENAI_API_KEY", "")
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

        if not api_key:
            from ccb.config import get_active_account
            acct = get_active_account()
            if acct:
                api_key = acct.get("apiKey", "")
                if acct.get("baseUrl"):
                    base_url = acct["baseUrl"]

        if not api_key:
            return ToolResult(
                output="Error: No API key found. Set OPENAI_API_KEY or configure an account.",
                is_error=True,
            )

        try:
            import httpx

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{base_url}/images/generations",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "dall-e-3",
                        "prompt": prompt,
                        "size": size,
                        "quality": quality,
                        "n": 1,
                        "response_format": "url",
                    },
                )

                if resp.status_code != 200:
                    return ToolResult(
                        output=f"API error ({resp.status_code}): {resp.text[:500]}",
                        is_error=True,
                    )

                data = resp.json()
                image_url = data["data"][0]["url"]
                revised_prompt = data["data"][0].get("revised_prompt", "")

                # Download the image
                img_resp = await client.get(image_url)
                if img_resp.status_code != 200:
                    return ToolResult(
                        output=f"Image download failed ({img_resp.status_code}). URL: {image_url}",
                        is_error=True,
                    )

                # Save to file
                if not filename:
                    import hashlib
                    import time
                    filename = f"img_{hashlib.md5(prompt.encode()).hexdigest()[:8]}_{int(time.time())}"

                # Detect format from content type
                content_type = img_resp.headers.get("content-type", "image/png")
                ext = ".png"
                if "jpeg" in content_type or "jpg" in content_type:
                    ext = ".jpg"
                elif "webp" in content_type:
                    ext = ".webp"

                output_path = os.path.join(cwd, f"{filename}{ext}")
                with open(output_path, "wb") as f:
                    f.write(img_resp.content)

                result_parts = [
                    f"Image saved to: {output_path}",
                    f"Size: {len(img_resp.content)} bytes",
                ]
                if revised_prompt:
                    result_parts.append(f"Revised prompt: {revised_prompt}")

                return ToolResult(output="\n".join(result_parts))

        except ImportError:
            return ToolResult(
                output="Error: httpx not installed. Run: pip install httpx",
                is_error=True,
            )
        except Exception as e:
            return ToolResult(output=f"Image generation error: {e}", is_error=True)
