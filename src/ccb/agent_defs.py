"""Agent definitions — reusable agent configurations.

Agent definitions are loaded from:
  1. ~/.claude/agents/*.yaml (user-global)
  2. .claude/agents/*.yaml  (project-level)

Each file defines an agent with a specific system prompt, tools, model,
and other settings, inspired by claude-agent-sdk's AgentDefinition.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentDef:
    """A reusable agent configuration."""
    name: str
    description: str = ""
    prompt: str = ""
    model: str | None = None
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    effort: str | None = None  # low, medium, high
    thinking: str | None = None  # off, on, adaptive
    thinking_budget: int = 10000
    memory: str | None = None  # user, project, local
    permission_mode: str | None = None
    max_turns: int | None = None
    source: str = ""  # file path

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _load_yaml(path: Path) -> dict[str, Any] | None:
    """Load a YAML file. Falls back to JSON if PyYAML unavailable."""
    try:
        import yaml
        return yaml.safe_load(path.read_text())
    except ImportError:
        pass
    # Fallback: try JSON
    import json
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def discover_agents(cwd: str = "") -> list[AgentDef]:
    """Discover agent definitions from global + project directories."""
    agents: list[AgentDef] = []
    seen_names: set[str] = set()

    dirs = []
    # Project-level
    if cwd:
        dirs.append(Path(cwd) / ".claude" / "agents")
    # User-global
    dirs.append(Path.home() / ".claude" / "agents")

    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix not in (".yaml", ".yml", ".json"):
                continue
            data = _load_yaml(f)
            if not data or not isinstance(data, dict):
                continue
            name = data.get("name", f.stem)
            if name in seen_names:
                continue
            seen_names.add(name)
            agents.append(AgentDef(
                name=name,
                description=data.get("description", ""),
                prompt=data.get("prompt", ""),
                model=data.get("model"),
                tools=data.get("tools"),
                disallowed_tools=data.get("disallowedTools") or data.get("disallowed_tools"),
                effort=data.get("effort"),
                thinking=data.get("thinking"),
                thinking_budget=data.get("thinkingBudget", data.get("thinking_budget", 10000)),
                memory=data.get("memory"),
                permission_mode=data.get("permissionMode") or data.get("permission_mode"),
                max_turns=data.get("maxTurns") or data.get("max_turns"),
                source=str(f),
            ))

    # Built-in agents
    if "coder" not in seen_names:
        agents.append(AgentDef(
            name="coder",
            description="Focused coding agent — code only, no explanations",
            prompt="You are a coding-only agent. Output code changes directly. No explanations unless asked.",
            effort="high",
            thinking="adaptive",
        ))
    if "reviewer" not in seen_names:
        agents.append(AgentDef(
            name="reviewer",
            description="Code reviewer — finds bugs, suggests improvements",
            prompt=(
                "You are a senior code reviewer. Review the code for bugs, security issues, "
                "performance problems, and style violations. Be specific and actionable."
            ),
            effort="high",
            thinking="on",
        ))
    if "planner" not in seen_names:
        agents.append(AgentDef(
            name="planner",
            description="Planning agent — creates detailed implementation plans",
            prompt=(
                "You are a planning agent. Create detailed, step-by-step implementation plans. "
                "Consider edge cases, dependencies, and testing strategy. Do NOT write code."
            ),
            effort="high",
            thinking="on",
        ))

    return agents


def get_agent(name: str, cwd: str = "") -> AgentDef | None:
    """Get a specific agent definition by name."""
    for agent in discover_agents(cwd):
        if agent.name == name:
            return agent
    return None


def apply_agent(agent: AgentDef, provider: Any, state: dict[str, Any]) -> str:
    """Apply an agent definition to the current provider and state.

    Returns the agent's system prompt addition (to prepend to the main prompt).
    """
    # Apply model change
    if agent.model and hasattr(provider, 'set_model'):
        provider.set_model(agent.model)
        state["_model_override"] = agent.model

    # Apply effort
    if agent.effort:
        state["effort"] = agent.effort

    # Apply thinking (only for providers that support it)
    if agent.thinking and getattr(provider, 'supports_thinking', False):
        if agent.thinking == "adaptive":
            provider.set_thinking(True, agent.thinking_budget, mode="adaptive")
            state["thinking"] = True
            state["thinking_mode"] = "adaptive"
        elif agent.thinking == "on":
            provider.set_thinking(True, agent.thinking_budget)
            state["thinking"] = True
            state["thinking_mode"] = "on"
        else:
            provider.set_thinking(False)
            state["thinking"] = False
            state["thinking_mode"] = "off"

    # Apply max_turns
    if agent.max_turns:
        state["max_tool_rounds"] = agent.max_turns

    state["_active_agent"] = agent.name
    return agent.prompt
