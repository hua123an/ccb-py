"""Ultra-plan: advanced multi-step planning for ccb-py.

Structured planning with dependency tracking, execution ordering,
and progress visualization.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


@dataclass
class PlanStep:
    id: str
    title: str
    description: str = ""
    status: StepStatus = StepStatus.PENDING
    depends_on: list[str] = field(default_factory=list)
    substeps: list[str] = field(default_factory=list)
    output: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    estimated_seconds: int = 0
    tags: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if self.started_at == 0:
            return 0
        end = self.completed_at if self.completed_at else time.time()
        return end - self.started_at


@dataclass
class Plan:
    id: str
    title: str
    description: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        done = sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)
        return done / len(self.steps)

    @property
    def status_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self.steps:
            counts[s.status.value] = counts.get(s.status.value, 0) + 1
        return counts

    def next_steps(self) -> list[PlanStep]:
        """Get steps that can be executed now (dependencies met)."""
        completed = {s.id for s in self.steps if s.status == StepStatus.COMPLETED}
        return [
            s for s in self.steps
            if s.status == StepStatus.PENDING
            and all(d in completed for d in s.depends_on)
        ]

    def critical_path(self) -> list[PlanStep]:
        """Find the critical path (longest dependency chain)."""
        # Simple topological sort based on dependencies
        visited: set[str] = set()
        path: list[PlanStep] = []
        step_map = {s.id: s for s in self.steps}

        def visit(sid: str) -> None:
            if sid in visited:
                return
            visited.add(sid)
            step = step_map.get(sid)
            if step:
                for dep in step.depends_on:
                    visit(dep)
                path.append(step)

        for s in self.steps:
            visit(s.id)
        return path

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "progress": self.progress,
            "steps": [asdict(s) for s in self.steps],
            "status_summary": self.status_summary,
        }

    def to_markdown(self) -> str:
        lines = [f"# {self.title}", ""]
        if self.description:
            lines += [self.description, ""]
        lines.append(f"Progress: {self.progress:.0%}\n")
        for s in self.steps:
            icon = {
                StepStatus.PENDING: "⬜",
                StepStatus.IN_PROGRESS: "🔄",
                StepStatus.COMPLETED: "✅",
                StepStatus.FAILED: "❌",
                StepStatus.SKIPPED: "⏭️",
                StepStatus.BLOCKED: "🚫",
            }.get(s.status, "⬜")
            deps = f" (after: {', '.join(s.depends_on)})" if s.depends_on else ""
            lines.append(f"{icon} **{s.id}**: {s.title}{deps}")
            if s.description:
                lines.append(f"   {s.description}")
        return "\n".join(lines)


class PlanManager:
    """Manages ultra-plans."""

    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}
        self._active: str | None = None

    def create_plan(self, title: str, description: str = "") -> Plan:
        pid = f"plan_{int(time.time())}"
        plan = Plan(id=pid, title=title, description=description, created_at=time.time())
        self._plans[pid] = plan
        self._active = pid
        return plan

    def add_step(
        self,
        plan_id: str | None,
        step_id: str,
        title: str,
        description: str = "",
        depends_on: list[str] | None = None,
    ) -> PlanStep | None:
        plan = self._plans.get(plan_id or self._active or "")
        if not plan:
            return None
        step = PlanStep(
            id=step_id, title=title, description=description,
            depends_on=depends_on or [],
        )
        plan.steps.append(step)
        plan.updated_at = time.time()
        return step

    def update_step(self, step_id: str, status: StepStatus, output: str = "",
                    plan_id: str | None = None) -> bool:
        plan = self._plans.get(plan_id or self._active or "")
        if not plan:
            return False
        for s in plan.steps:
            if s.id == step_id:
                s.status = status
                if status == StepStatus.IN_PROGRESS and s.started_at == 0:
                    s.started_at = time.time()
                if status in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED):
                    s.completed_at = time.time()
                if output:
                    s.output = output
                # Update blocked steps
                self._update_blocked(plan)
                return True
        return False

    def _update_blocked(self, plan: Plan) -> None:
        failed = {s.id for s in plan.steps if s.status == StepStatus.FAILED}
        for s in plan.steps:
            if s.status == StepStatus.PENDING and any(d in failed for d in s.depends_on):
                s.status = StepStatus.BLOCKED

    def get_plan(self, plan_id: str | None = None) -> Plan | None:
        return self._plans.get(plan_id or self._active or "")

    def list_plans(self) -> list[Plan]:
        return sorted(self._plans.values(), key=lambda p: p.created_at, reverse=True)

    def delete_plan(self, plan_id: str) -> bool:
        if plan_id in self._plans:
            del self._plans[plan_id]
            if self._active == plan_id:
                self._active = None
            return True
        return False

    @property
    def active_plan(self) -> Plan | None:
        return self._plans.get(self._active or "")


# Prompt for LLM to generate a plan
def generate_plan_prompt(objective: str) -> str:
    return (
        "Create a detailed execution plan for the following objective.\n"
        "Format as JSON:\n"
        "```json\n"
        '{"title": "...", "steps": [\n'
        '  {"id": "step1", "title": "...", "description": "...", "depends_on": []},\n'
        '  {"id": "step2", "title": "...", "description": "...", "depends_on": ["step1"]}\n'
        "]}\n```\n\n"
        f"Objective: {objective}\n\n"
        "Reply with ONLY the JSON."
    )


# Module singleton
_manager: PlanManager | None = None


def get_plan_manager() -> PlanManager:
    global _manager
    if _manager is None:
        _manager = PlanManager()
    return _manager
