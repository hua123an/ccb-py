"""Skills and workflow loading plus shared resolution helpers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ccb.config import claude_dir

SKILL_KIND = "skill"
WORKFLOW_KIND = "workflow"


@dataclass
class Skill:
    name: str
    description: str
    prompt: str
    source: str  # "project" | "user" | "bundled"
    kind: str = SKILL_KIND  # "skill" | "workflow"
    path: str | None = None

    @property
    def slash_command(self) -> str:
        return f"/{self.name}"

    @property
    def invocation_command(self) -> str:
        return f"/workflows {self.name}" if self.kind == WORKFLOW_KIND else f"/skills {self.name}"

    @property
    def source_label(self) -> str:
        return self.kind if self.kind == WORKFLOW_KIND else self.source


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
    skills: list[Skill] = []
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
                kind=SKILL_KIND,
                path=str(f),
            ))
        except Exception:
            continue
    return skills


def _load_workflows(wf_dir: Path, source: str) -> list[Skill]:
    """Load workflows as skills."""
    skills: list[Skill] = []
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
                kind=WORKFLOW_KIND,
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


def normalize_skill_kind(kind: str | None) -> str | None:
    if kind is None:
        return None
    lowered = kind.strip().lower()
    if lowered in {SKILL_KIND, WORKFLOW_KIND}:
        return lowered
    raise ValueError(f"Invalid skill kind: {kind}")


def _coerce_skill_kind(kind: str | None) -> str | None:
    try:
        return normalize_skill_kind(kind)
    except ValueError:
        return None


def list_skills(cwd: str, *, kind: str | None = None) -> list[Skill]:
    wanted_kind = _coerce_skill_kind(kind)
    skills = load_skills(cwd)
    if wanted_kind is None:
        return skills
    return [skill for skill in skills if skill.kind == wanted_kind]


def find_skill(cwd: str, name: str, *, kind: str | None = None) -> Skill | None:
    wanted_kind = _coerce_skill_kind(kind)
    wanted = name.strip().lstrip("/").lower()
    if not wanted:
        return None
    for skill in list_skills(cwd, kind=wanted_kind):
        if skill.name.lower() == wanted:
            return skill
    return None


def search_skills(
    cwd: str,
    query: str,
    *,
    kind: str | None = None,
    limit: int = 8,
) -> list[Skill]:
    wanted_kind = _coerce_skill_kind(kind)
    wanted = query.strip().lower()
    if not wanted:
        return list_skills(cwd, kind=wanted_kind)[:limit]

    terms = wanted.split()
    scored: list[tuple[tuple[int, int, str], Skill]] = []
    for skill in list_skills(cwd, kind=wanted_kind):
        haystacks = (skill.name.lower(), skill.description.lower())
        if wanted == skill.name.lower():
            score = (0, len(skill.name), skill.name)
        elif wanted in skill.name.lower():
            score = (1, len(skill.name), skill.name)
        elif all(any(term in hay for hay in haystacks) for term in terms):
            score = (2, len(skill.name), skill.name)
        elif any(term in hay for term in terms for hay in haystacks):
            score = (3, len(skill.name), skill.name)
        else:
            continue
        scored.append((score, skill))

    scored.sort(key=lambda item: item[0])
    return [skill for _, skill in scored[:limit]]


def resolve_skill_reference(
    cwd: str,
    raw: str,
    *,
    kind: str | None = None,
) -> tuple[Skill, str] | None:
    trimmed = raw.strip()
    if not trimmed:
        return None

    name, _, rest = trimmed.partition(" ")
    skill = find_skill(cwd, name, kind=kind)
    if skill is None:
        return None
    return skill, rest.strip()


def build_skill_prompt(skill: Skill, args: str | dict[str, object] | None = None) -> str:
    prompt = skill.prompt.rstrip()
    if args is None:
        return prompt
    if isinstance(args, dict):
        if not args:
            return prompt
        arg_text = json.dumps(args, ensure_ascii=False, indent=2, sort_keys=True)
        return f"{prompt}\n\nArguments:\n{arg_text}"

    extra = str(args).strip()
    if not extra:
        return prompt
    return f"{prompt}\n\nAdditional context:\n{extra}"


def resolve_skill_prompt(
    cwd: str,
    name: str,
    args: str | dict[str, object] | None = None,
    *,
    kind: str | None = None,
) -> tuple[Skill, str] | None:
    skill = find_skill(cwd, name, kind=kind)
    if skill is None:
        return None
    return skill, build_skill_prompt(skill, args)


def skill_metadata(skill: Skill) -> dict[str, str | None]:
    return {
        "name": skill.name,
        "description": skill.description,
        "source": skill.source,
        "kind": skill.kind,
        "path": skill.path,
        "slash_command": skill.slash_command,
        "invocation_command": skill.invocation_command,
    }


def build_skill_command_map(cwd: str) -> dict[str, str]:
    commands: dict[str, str] = {}
    for skill in load_skills(cwd):
        commands.setdefault(
            skill.slash_command,
            f"[{skill.kind}] {skill.description}",
        )
    return commands


def build_skill_invocation_map(cwd: str) -> dict[str, str]:
    commands: dict[str, str] = {}
    for skill in load_skills(cwd):
        commands[skill.invocation_command] = skill.description
    return commands
