"""Per-agent execution context.

We use a single ``ContextVar`` to mark whether the current asyncio task is
running INSIDE a subagent's execution stack. contextvars automatically
propagate through ``asyncio.create_task``, ``await``, and ``gather``, so
setting the flag at the top of ``run_agent`` cascades to every tool call
made by that agent — without any explicit plumbing.

This enables:
  - Permission bypass inside subagents (so parallel agents don't bombard
    the user with N permission prompts).
  - Compact/suppressed tool output inside subagents (so N agents' chatter
    doesn't interleave unreadably in the parent REPL).

Both behaviors are opt-in: any code can call ``is_inside_agent()`` to
branch on the current context.
"""
from __future__ import annotations

import contextvars


# When True, the current asyncio task is executing inside a spawned agent.
# Default False (main REPL / top-level run_turn).
_inside_agent: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ccb_inside_agent", default=False
)

# Optional human-readable label for the current agent (e.g. "2/5 · Audit auth").
# Used by progress UI when suppressing normal tool output.
_agent_label: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ccb_agent_label", default=""
)


def is_inside_agent() -> bool:
    return _inside_agent.get()


def current_agent_label() -> str:
    return _agent_label.get()


def enter_agent(label: str = "") -> None:
    """Mark the current asyncio task as running inside an agent.

    Should be called at the top of ``run_agent``. Because contextvars copy
    on ``asyncio.create_task`` creation, this ``set()`` is isolated to the
    agent's own task tree and doesn't leak to siblings or the parent. No
    explicit "exit" call is needed — the task's context goes out of scope
    when the task completes.
    """
    _inside_agent.set(True)
    if label:
        _agent_label.set(label)
