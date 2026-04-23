"""UpstreamProxy — HTTP proxy relay for API calls.

Provides an optional local HTTP proxy that relays API requests to the
upstream provider, adding auth headers, rate-limit handling, and request
logging. Useful for debugging, corporate proxies, and multi-tenant setups.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProxyStats:
    """Proxy request/response statistics."""
    total_requests: int = 0
    total_errors: int = 0
    total_bytes_sent: int = 0
    total_bytes_received: int = 0
    last_request_at: float = 0.0
    avg_latency_ms: float = 0.0
    _latencies: list[float] = field(default_factory=list)

    def record_request(self, latency_ms: float, bytes_sent: int, bytes_recv: int) -> None:
        self.total_requests += 1
        self.total_bytes_sent += bytes_sent
        self.total_bytes_received += bytes_recv
        self.last_request_at = time.time()
        self._latencies.append(latency_ms)
        if len(self._latencies) > 100:
            self._latencies = self._latencies[-100:]
        self.avg_latency_ms = sum(self._latencies) / len(self._latencies)

    def record_error(self) -> None:
        self.total_errors += 1


@dataclass
class ProxyConfig:
    """Upstream proxy configuration."""
    listen_host: str = "127.0.0.1"
    listen_port: int = 8901
    upstream_url: str = ""  # e.g. "https://api.anthropic.com"
    api_key: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)
    log_requests: bool = False
    max_retries: int = 2
    timeout_seconds: float = 120.0


class UpstreamProxy:
    """Local HTTP proxy that relays requests to upstream API providers."""

    def __init__(self, config: ProxyConfig | None = None) -> None:
        self.config = config or ProxyConfig()
        self.stats = ProxyStats()
        self._server: Any = None
        self._running = False

    async def handle_request(self, request: Any) -> Any:
        """Handle a proxied request."""
        try:
            import aiohttp
            from aiohttp import web
        except ImportError:
            raise RuntimeError("aiohttp required for upstream proxy")

        start = time.time()
        path = request.path
        method = request.method
        body = await request.read()

        # Build upstream URL
        upstream = self.config.upstream_url.rstrip("/") + path
        headers = dict(request.headers)
        headers.pop("Host", None)

        # Add auth
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key
            headers["anthropic-version"] = "2023-06-01"

        # Add extra headers
        for k, v in self.config.extra_headers.items():
            headers[k] = v

        if self.config.log_requests:
            logger.info("PROXY %s %s (%d bytes)", method, upstream, len(body))

        # Relay
        retries = 0
        last_error = None
        while retries <= self.config.max_retries:
            try:
                timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.request(
                        method, upstream,
                        headers=headers,
                        data=body if body else None,
                    ) as resp:
                        resp_body = await resp.read()
                        latency = (time.time() - start) * 1000
                        self.stats.record_request(
                            latency, len(body), len(resp_body)
                        )

                        if self.config.log_requests:
                            logger.info(
                                "PROXY → %d (%d bytes, %.0fms)",
                                resp.status, len(resp_body), latency,
                            )

                        return web.Response(
                            status=resp.status,
                            body=resp_body,
                            headers={
                                k: v for k, v in resp.headers.items()
                                if k.lower() not in ("transfer-encoding", "content-encoding")
                            },
                        )

            except Exception as e:
                last_error = e
                retries += 1
                if retries <= self.config.max_retries:
                    await asyncio.sleep(0.5 * retries)

        self.stats.record_error()
        from aiohttp import web
        return web.Response(
            status=502,
            text=f"Upstream proxy error: {last_error}",
        )

    async def start(self) -> str:
        """Start the proxy server. Returns the listen URL."""
        try:
            from aiohttp import web
        except ImportError:
            raise RuntimeError("aiohttp required for upstream proxy")

        app = web.Application()
        app.router.add_route("*", "/{path_info:.*}", self.handle_request)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(
            runner,
            self.config.listen_host,
            self.config.listen_port,
        )
        await site.start()
        self._server = runner
        self._running = True

        url = f"http://{self.config.listen_host}:{self.config.listen_port}"
        logger.info("Upstream proxy listening on %s → %s", url, self.config.upstream_url)
        return url

    async def stop(self) -> None:
        """Stop the proxy server."""
        if self._server:
            await self._server.cleanup()
            self._server = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def summary(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "upstream": self.config.upstream_url,
            "listen": f"{self.config.listen_host}:{self.config.listen_port}",
            "stats": {
                "requests": self.stats.total_requests,
                "errors": self.stats.total_errors,
                "avg_latency_ms": round(self.stats.avg_latency_ms, 1),
                "bytes_sent": self.stats.total_bytes_sent,
                "bytes_received": self.stats.total_bytes_received,
            },
        }
