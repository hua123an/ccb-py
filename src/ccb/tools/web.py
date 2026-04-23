"""Web tools - fetch URLs and search the web."""
from __future__ import annotations

from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import WEB_FETCH_PROMPT, WEB_SEARCH_PROMPT


class WebFetchTool(Tool):
    name = "web_fetch"
    description = WEB_FETCH_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch."},
        },
        "required": ["url"],
    }

    @property
    def needs_permission(self) -> bool:
        return True

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        url = input.get("url", "")
        if not url:
            return ToolResult(output="Error: no URL", is_error=True)

        try:
            import httpx
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                resp = await client.get(url, headers={"User-Agent": "CCB/0.1"})
                content_type = resp.headers.get("content-type", "")

                if "text/html" in content_type:
                    # Simple HTML to text conversion
                    text = self._html_to_text(resp.text)
                else:
                    text = resp.text

                if len(text) > 100_000:
                    text = text[:100_000] + "\n... (truncated)"
                return ToolResult(output=text)
        except Exception as e:
            return ToolResult(output=f"Fetch error: {e}", is_error=True)

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Minimal HTML to text."""
        import re
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text


class WebSearchTool(Tool):
    name = "web_search"
    description = WEB_SEARCH_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
        },
        "required": ["query"],
    }

    @property
    def needs_permission(self) -> bool:
        return True

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        query = input.get("query", "")
        if not query:
            return ToolResult(output="Error: no query", is_error=True)

        # Use DuckDuckGo HTML search (no API key needed)
        try:
            import httpx
            url = "https://html.duckduckgo.com/html/"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, data={"q": query}, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; CCB/0.1)",
                })
                return ToolResult(output=self._parse_ddg(resp.text))
        except Exception as e:
            return ToolResult(output=f"Search error: {e}", is_error=True)

    @staticmethod
    def _parse_ddg(html: str) -> str:
        """Extract search results from DuckDuckGo HTML."""
        import re
        results = []
        # Find result blocks
        for match in re.finditer(
            r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'class="result__snippet"[^>]*>(.*?)</',
            html, re.DOTALL,
        ):
            url = match.group(1)
            title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()
            if title:
                results.append(f"**{title}**\n{url}\n{snippet}\n")
            if len(results) >= 8:
                break
        return "\n".join(results) if results else "No results found."
