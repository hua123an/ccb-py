"""Skills and Workflows - load custom prompt commands from .claude/skills/ and .windsurf/workflows/."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ccb.config import claude_dir


@dataclass
class Skill:
    name: str
    description: str
    prompt: str
    source: str  # "project" | "user" | "bundled"
    path: str | None = None


def load_skills(cwd: str) -> list[Skill]:
    """Load skills from all sources."""
    skills: list[Skill] = []
    skills.extend(_load_bundled_skills())
    skills.extend(_load_dir_skills(claude_dir() / "skills", "user"))
    skills.extend(_load_dir_skills(Path(cwd) / ".claude" / "skills", "project"))
    # Also load workflows
    skills.extend(_load_workflows(Path(cwd) / ".windsurf" / "workflows", "project"))
    skills.extend(_load_workflows(Path(cwd) / ".claude" / "workflows", "project"))
    return skills


def _load_bundled_skills() -> list[Skill]:
    """Built-in skills."""
    return [
        Skill(
            name="review",
            description="Review code changes for bugs, style issues, and improvements",
            prompt=(
                "Review the current code changes (use git diff). Look for:\n"
                "1. Bugs and logic errors\n2. Security issues\n3. Performance problems\n"
                "4. Style inconsistencies\n5. Missing error handling\n"
                "Provide specific, actionable feedback."
            ),
            source="bundled",
        ),
        Skill(
            name="test",
            description="Generate tests for recent changes",
            prompt=(
                "Look at the recent code changes (git diff) and generate appropriate tests. "
                "Follow existing test patterns in the project. "
                "Run the tests to make sure they pass."
            ),
            source="bundled",
        ),
        Skill(
            name="explain",
            description="Explain the current codebase structure",
            prompt=(
                "Analyze the current project structure. Explain:\n"
                "1. What this project does\n2. Key directories and files\n"
                "3. Main technologies used\n4. How to build and run it"
            ),
            source="bundled",
        ),
    ]


def _load_dir_skills(skills_dir: Path, source: str) -> list[Skill]:
    """Load skills from a directory of .md files."""
    skills = []
    if not skills_dir.exists():
        return skills

    for f in skills_dir.glob("*.md"):
        try:
            content = f.read_text()
            name = f.stem
            description, prompt = _parse_skill_md(content)
            skills.append(Skill(
                name=name,
                description=description or f"Custom skill: {name}",
                prompt=prompt,
                source=source,
                path=str(f),
            ))
        except Exception:
            continue
    return skills


def _load_workflows(wf_dir: Path, source: str) -> list[Skill]:
    """Load workflows as skills."""
    skills = []
    if not wf_dir.exists():
        return skills

    for f in wf_dir.glob("*.md"):
        try:
            content = f.read_text()
            name = f.stem
            description, body = _parse_workflow_md(content)
            skills.append(Skill(
                name=name,
                description=description or f"Workflow: {name}",
                prompt=f"Follow these workflow steps:\n\n{body}",
                source=source,
                path=str(f),
            ))
        except Exception:
            continue
    return skills


def _parse_skill_md(content: str) -> tuple[str, str]:
    """Parse a skill .md file. Returns (description, prompt)."""
    # Check for YAML frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            body = parts[2].strip()
            desc = ""
            for line in frontmatter.splitlines():
                if line.strip().startswith("description:"):
                    desc = line.split(":", 1)[1].strip().strip("\"'")
            return desc, body
    return "", content.strip()


def _parse_workflow_md(content: str) -> tuple[str, str]:
    """Parse a workflow .md file with YAML frontmatter."""
    return _parse_skill_md(content)  # Same format
