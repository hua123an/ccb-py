"""Analytics and telemetry for ccb-py.

Tracks usage statistics locally. Optionally integrates with
Langfuse for LLM observability.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class UsageEvent:
    event_type: str  # "command", "tool_call", "api_call", "error", "session"
    name: str
    timestamp: float = 0.0
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionStats:
    session_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    messages: int = 0
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    tools_used: dict[str, int] = field(default_factory=dict)
    commands_used: dict[str, int] = field(default_factory=dict)
    models_used: dict[str, int] = field(default_factory=dict)
    errors: int = 0


class AnalyticsTracker:
    """Local analytics tracker with optional Langfuse integration."""

    def __init__(self, data_dir: Path | None = None, enabled: bool = True):
        self.enabled = enabled
        self._dir = data_dir or (Path.home() / ".claude" / "analytics")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._events: list[UsageEvent] = []
        self._session = SessionStats()
        self._langfuse: Any = None

    def start_session(self, session_id: str) -> None:
        self._session = SessionStats(
            session_id=session_id,
            start_time=time.time(),
        )

    def end_session(self) -> SessionStats:
        self._session.end_time = time.time()
        self._save_session()
        return self._session

    def track_event(self, event_type: str, name: str, **metadata: Any) -> None:
        if not self.enabled:
            return
        event = UsageEvent(
            event_type=event_type,
            name=name,
            timestamp=time.time(),
            metadata=metadata,
        )
        self._events.append(event)

        # Update session counters
        if event_type == "tool_call":
            self._session.tools_used[name] = self._session.tools_used.get(name, 0) + 1
        elif event_type == "command":
            self._session.commands_used[name] = self._session.commands_used.get(name, 0) + 1
        elif event_type == "api_call":
            self._session.turns += 1
            tokens = metadata.get("input_tokens", 0)
            out_tokens = metadata.get("output_tokens", 0)
            self._session.input_tokens += tokens
            self._session.output_tokens += out_tokens
            model = metadata.get("model", "unknown")
            self._session.models_used[model] = self._session.models_used.get(model, 0) + 1
        elif event_type == "error":
            self._session.errors += 1

    def track_cost(self, cost_usd: float) -> None:
        self._session.cost_usd += cost_usd

    def track_message(self) -> None:
        self._session.messages += 1

    def _save_session(self) -> None:
        if not self._session.session_id:
            return
        try:
            path = self._dir / f"{self._session.session_id}.json"
            path.write_text(json.dumps(asdict(self._session), indent=2, default=str))
        except OSError:
            pass

    def _save_events(self) -> None:
        if not self._events:
            return
        try:
            path = self._dir / f"events_{int(time.time())}.jsonl"
            with path.open("a") as f:
                for e in self._events:
                    f.write(json.dumps(asdict(e), default=str) + "\n")
            self._events.clear()
        except OSError:
            pass

    # ── Reporting ──

    def get_session_stats(self) -> dict[str, Any]:
        return asdict(self._session)

    def get_historical_stats(self, days: int = 7) -> dict[str, Any]:
        """Aggregate stats from recent session files."""
        cutoff = time.time() - days * 86400
        total = SessionStats()
        count = 0
        try:
            for f in sorted(self._dir.glob("*.json"), reverse=True):
                if f.name.startswith("events_"):
                    continue
                try:
                    data = json.loads(f.read_text())
                    if data.get("start_time", 0) < cutoff:
                        break
                    count += 1
                    total.messages += data.get("messages", 0)
                    total.turns += data.get("turns", 0)
                    total.input_tokens += data.get("input_tokens", 0)
                    total.output_tokens += data.get("output_tokens", 0)
                    total.cost_usd += data.get("cost_usd", 0)
                    total.errors += data.get("errors", 0)
                    for k, v in data.get("tools_used", {}).items():
                        total.tools_used[k] = total.tools_used.get(k, 0) + v
                    for k, v in data.get("commands_used", {}).items():
                        total.commands_used[k] = total.commands_used.get(k, 0) + v
                except (json.JSONDecodeError, OSError):
                    continue
        except OSError:
            pass
        return {
            "sessions": count,
            "days": days,
            **asdict(total),
        }

    # ── Langfuse integration ──

    def init_langfuse(self, public_key: str, secret_key: str, host: str = "https://cloud.langfuse.com") -> bool:
        try:
            from langfuse import Langfuse
            self._langfuse = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
            return True
        except ImportError:
            return False

    def langfuse_trace(self, name: str, **kwargs: Any) -> Any:
        if self._langfuse:
            return self._langfuse.trace(name=name, **kwargs)
        return None

    def langfuse_generation(
        self,
        trace_id: str,
        name: str,
        model: str,
        input_text: str,
        output_text: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: float = 0,
    ) -> Any:
        """Log an LLM generation to Langfuse."""
        if not self._langfuse:
            return None
        try:
            return self._langfuse.generation(
                trace_id=trace_id,
                name=name,
                model=model,
                input=input_text[:5000],
                output=output_text[:5000],
                usage={
                    "input": input_tokens,
                    "output": output_tokens,
                    "total": input_tokens + output_tokens,
                },
                metadata={"duration_ms": duration_ms},
            )
        except Exception:
            return None

    def langfuse_span(self, trace_id: str, name: str, **kwargs: Any) -> Any:
        if not self._langfuse:
            return None
        try:
            return self._langfuse.span(trace_id=trace_id, name=name, **kwargs)
        except Exception:
            return None

    def langfuse_event(self, trace_id: str, name: str, **kwargs: Any) -> Any:
        if not self._langfuse:
            return None
        try:
            return self._langfuse.event(trace_id=trace_id, name=name, **kwargs)
        except Exception:
            return None

    def langfuse_flush(self) -> None:
        if self._langfuse:
            try:
                self._langfuse.flush()
            except Exception:
                pass

    # ── Performance metrics ──

    def track_latency(self, name: str, start_time: float) -> float:
        """Track and record latency for an operation."""
        latency = (time.time() - start_time) * 1000  # ms
        self.track_event("latency", name, latency_ms=latency)
        return latency

    def get_latency_stats(self) -> dict[str, Any]:
        """Get average latency by event name."""
        latencies: dict[str, list[float]] = {}
        for e in self._events:
            if e.event_type == "latency":
                if e.name not in latencies:
                    latencies[e.name] = []
                latencies[e.name].append(e.metadata.get("latency_ms", 0))

        stats = {}
        for name, values in latencies.items():
            if values:
                stats[name] = {
                    "avg_ms": round(sum(values) / len(values), 1),
                    "min_ms": round(min(values), 1),
                    "max_ms": round(max(values), 1),
                    "count": len(values),
                }
        return stats

    # ── Export ──

    def export_csv(self, output_path: str | None = None) -> str:
        """Export session stats as CSV."""
        import csv
        import io

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["session_id", "start", "messages", "turns", "input_tokens",
                         "output_tokens", "cost_usd", "errors"])

        for f in sorted(self._dir.glob("*.json")):
            if f.name.startswith("events_"):
                continue
            try:
                d = json.loads(f.read_text())
                writer.writerow([
                    d.get("session_id", ""),
                    d.get("start_time", ""),
                    d.get("messages", 0),
                    d.get("turns", 0),
                    d.get("input_tokens", 0),
                    d.get("output_tokens", 0),
                    d.get("cost_usd", 0),
                    d.get("errors", 0),
                ])
            except (json.JSONDecodeError, OSError):
                continue

        content = buf.getvalue()
        if output_path:
            Path(output_path).write_text(content)
        return content


# Module singleton
_tracker: AnalyticsTracker | None = None


def get_tracker() -> AnalyticsTracker:
    global _tracker
    if _tracker is None:
        _tracker = AnalyticsTracker()
    return _tracker
