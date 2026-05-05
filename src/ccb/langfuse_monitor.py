"""Langfuse monitoring integration for agent observability.

Uses httpx for direct API calls (no langfuse package required).
Gracefully degrades if httpx is not installed or API is unreachable.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_HAS_HTTPX = True
try:
    import httpx
except ImportError:
    _HAS_HTTPX = False


@dataclass
class _PendingEvent:
    """A buffered event waiting to be flushed."""
    body: dict[str, Any]
    endpoint: str  # "/api/public/ingestion" style


class LangfuseMonitor:
    """Langfuse integration for tracing agent turns, spans, and generations.

    Uses direct HTTP calls via httpx — does not require the ``langfuse``
    Python package.  All public methods are safe to call even when the
    monitor is disabled or httpx is missing; they simply become no-ops.

    Auto-flush policy:
      - Flush when the buffer reaches *batch_size* events (default 50).
      - Flush when *flush_interval* seconds have elapsed (default 10).
      - A background daemon thread handles timed flushes.
    """

    def __init__(
        self,
        api_key: str = "",
        host: str = "https://cloud.langfuse.com",
        public_key: str = "",
        batch_size: int = 50,
        flush_interval: float = 10.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.public_key = public_key or os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        self.secret_key = api_key or os.environ.get("LANGFUSE_SECRET_KEY", "")
        self.enabled = bool(self.public_key and self.secret_key and _HAS_HTTPX)
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._buffer: list[_PendingEvent] = []
        self._lock = threading.Lock()
        self._traces: dict[str, dict[str, Any]] = {}
        self._spans: dict[str, dict[str, Any]] = {}

        # Background flush timer
        self._timer: threading.Timer | None = None
        if self.enabled:
            self._start_timer()

        if not _HAS_HTTPX:
            log.debug("LangfuseMonitor: httpx not installed, monitoring disabled")
        elif not (self.public_key and self.secret_key):
            log.debug("LangfuseMonitor: missing credentials, monitoring disabled")

    # ── Trace lifecycle ──────────────────────────────────────────

    def trace_start(
        self,
        trace_id: str = "",
        name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Start a new trace. Returns the trace_id."""
        if not self.enabled:
            return trace_id or ""
        trace_id = trace_id or uuid.uuid4().hex
        body: dict[str, Any] = {
            "id": trace_id,
            "name": name or "ccb_turn",
            "timestamp": _iso_now(),
            "metadata": metadata or {},
        }
        self._traces[trace_id] = body
        self._enqueue(body, "/api/public/ingestion")
        return trace_id

    def trace_end(self, trace_id: str) -> None:
        """End (update) a trace with completion timestamp."""
        if not self.enabled or trace_id not in self._traces:
            return
        body = {
            "id": uuid.uuid4().hex,
            "traceId": trace_id,
            "type": "trace-update",
            "endTime": _iso_now(),
        }
        self._enqueue(body, "/api/public/ingestion")
        self._traces.pop(trace_id, None)

    # ── Span lifecycle ───────────────────────────────────────────

    def span_start(
        self,
        parent_id: str,
        name: str,
        input: Any = None,
    ) -> str:
        """Start a span under a trace. Returns the span_id."""
        if not self.enabled:
            return ""
        span_id = uuid.uuid4().hex
        body: dict[str, Any] = {
            "id": span_id,
            "traceId": parent_id,
            "name": name,
            "startTime": _iso_now(),
            "input": _safe_json(input),
        }
        self._spans[span_id] = body
        self._enqueue(body, "/api/public/ingestion")
        return span_id

    def span_end(
        self,
        span_id: str,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """End a span with output and optional metadata."""
        if not self.enabled or span_id not in self._spans:
            return
        body: dict[str, Any] = {
            "id": span_id,
            "endTime": _iso_now(),
            "output": _safe_json(output),
        }
        if metadata:
            body["metadata"] = metadata
        self._enqueue(body, "/api/public/ingestion")
        self._spans.pop(span_id, None)

    # ── Generation logging ───────────────────────────────────────

    def generation_log(
        self,
        trace_id: str,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: float = 0.0,
    ) -> None:
        """Log a model generation event with usage metrics."""
        if not self.enabled:
            return
        body: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "traceId": trace_id,
            "type": "generation",
            "name": "api_call",
            "startTime": _iso_now(),
            "model": model,
            "usage": {
                "input": input_tokens,
                "output": output_tokens,
                "total": input_tokens + output_tokens,
            },
            "metadata": {"latency_ms": latency_ms},
        }
        self._enqueue(body, "/api/public/ingestion")

    # ── Buffer management ────────────────────────────────────────

    def flush(self) -> None:
        """Send all buffered events to Langfuse. Thread-safe."""
        if not self.enabled or not _HAS_HTTPX:
            return
        with self._lock:
            batch = list(self._buffer)
            self._buffer.clear()
        if not batch:
            return
        self._send_batch(batch)

    def shutdown(self) -> None:
        """Flush remaining events and stop the background timer."""
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self.flush()

    # ── Private helpers ──────────────────────────────────────────

    def _enqueue(self, body: dict[str, Any], endpoint: str) -> None:
        with self._lock:
            self._buffer.append(_PendingEvent(body=body, endpoint=endpoint))
            if len(self._buffer) >= self.batch_size:
                batch = list(self._buffer)
                self._buffer.clear()
            else:
                batch = []
        if batch:
            self._send_batch(batch)

    def _send_batch(self, batch: list[_PendingEvent]) -> None:
        """POST a batch of events to the Langfuse ingestion endpoint."""
        if not _HAS_HTTPX:
            return
        url = f"{self.host}/api/public/ingestion"
        payload = {"batch": [ev.body for ev in batch]}
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    url,
                    json=payload,
                    auth=(self.public_key, self.secret_key),
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code >= 400:
                    log.warning(
                        "Langfuse flush failed: %d %s",
                        resp.status_code,
                        resp.text[:200],
                    )
        except Exception as exc:
            log.debug("Langfuse flush error: %s", exc)

    def _start_timer(self) -> None:
        self._timer = threading.Timer(self.flush_interval, self._timer_tick)
        self._timer.daemon = True
        self._timer.start()

    def _timer_tick(self) -> None:
        try:
            self.flush()
        finally:
            if self.enabled:
                self._start_timer()


# ── Module-level singleton ───────────────────────────────────────

_monitor: LangfuseMonitor | None = None


def get_monitor() -> LangfuseMonitor:
    """Get or create the global LangfuseMonitor singleton."""
    global _monitor
    if _monitor is None:
        _monitor = LangfuseMonitor()
    return _monitor


def init_monitor(**kwargs: Any) -> LangfuseMonitor:
    """Initialize (or re-initialize) the global monitor with explicit config."""
    global _monitor
    if _monitor is not None:
        _monitor.shutdown()
    _monitor = LangfuseMonitor(**kwargs)
    return _monitor


# ── Utilities ────────────────────────────────────────────────────

def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def _safe_json(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    try:
        return json.dumps(obj, default=str)[:10000]
    except Exception:
        return str(obj)[:10000]
