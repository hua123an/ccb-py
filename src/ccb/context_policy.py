"""Explicit context-management policy for collapse, offload, and compaction."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextPolicy:
    collapse_trigger_ratio: float = 0.72
    mild_offload_ratio: float = 0.70
    aggressive_offload_ratio: float = 0.85


def get_context_policy() -> ContextPolicy:
    """Return the current context-management policy."""
    return ContextPolicy()


def should_trigger_offload(context_ratio: float, policy: ContextPolicy | None = None) -> tuple[bool, str]:
    """Determine whether context offload/compaction should run."""
    active_policy = policy or get_context_policy()
    if context_ratio >= active_policy.aggressive_offload_ratio:
        return True, "Aggressive compress: context above 85%"
    if context_ratio >= active_policy.mild_offload_ratio:
        return True, "Mild offload: context above 70%"
    return False, ""
