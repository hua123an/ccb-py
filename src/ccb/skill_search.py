"""Skill search and recommendation engine for ccb-py.

Indexes available skills and provides search/matching based
on user queries.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillMatch:
    name: str
    source: str  # "bundled", "custom", "plugin", "workflow"
    description: str
    score: float = 0.0
    slash_command: str = ""
    tags: list[str] = field(default_factory=list)


class SkillSearchEngine:
    """Search and rank skills by relevance."""

    def __init__(self) -> None:
        self._skills: list[SkillMatch] = []
        self._indexed = False

    def index(self) -> int:
        """Build the skill index from all available sources."""
        self._skills.clear()

        # Bundled skills
        try:
            from ccb.skills import BUNDLED_SKILLS
            for name, info in BUNDLED_SKILLS.items():
                self._skills.append(SkillMatch(
                    name=name,
                    source="bundled",
                    description=info.get("description", ""),
                    slash_command=f"/skills {name}" if name else "",
                    tags=info.get("tags", []),
                ))
        except Exception:
            pass

        # Custom skills from ~/.claude/skills/
        try:
            from ccb.skills import discover_custom_skills
            for skill in discover_custom_skills():
                self._skills.append(SkillMatch(
                    name=skill.get("name", ""),
                    source="custom",
                    description=skill.get("description", ""),
                    slash_command=f"/skills {skill.get('name', '')}",
                ))
        except Exception:
            pass

        # Plugin skills
        try:
            from ccb.plugins import discover_plugin_slash_commands
            for cmd, info in discover_plugin_slash_commands().items():
                self._skills.append(SkillMatch(
                    name=cmd.lstrip("/"),
                    source="plugin",
                    description=info.get("description", ""),
                    slash_command=cmd,
                ))
        except Exception:
            pass

        # Workflows
        try:
            from ccb.skills import discover_workflows
            for wf in discover_workflows():
                self._skills.append(SkillMatch(
                    name=wf.get("name", ""),
                    source="workflow",
                    description=wf.get("description", ""),
                    slash_command=f"/workflows {wf.get('name', '')}",
                ))
        except Exception:
            pass

        self._indexed = True
        return len(self._skills)

    def search(self, query: str, limit: int = 10) -> list[SkillMatch]:
        """Search skills by keyword relevance."""
        if not self._indexed:
            self.index()

        query_lower = query.lower()
        terms = query_lower.split()
        results: list[SkillMatch] = []

        for skill in self._skills:
            score = 0.0
            searchable = f"{skill.name} {skill.description} {' '.join(skill.tags)}".lower()

            # Exact name match
            if query_lower == skill.name.lower():
                score += 10.0
            # Name contains query
            elif query_lower in skill.name.lower():
                score += 5.0

            # Term matching
            for term in terms:
                if term in skill.name.lower():
                    score += 3.0
                if term in skill.description.lower():
                    score += 1.0
                if any(term in t.lower() for t in skill.tags):
                    score += 2.0

            if score > 0:
                skill.score = score
                results.append(skill)

        results.sort(key=lambda s: s.score, reverse=True)
        return results[:limit]

    def recommend(self, context: str, limit: int = 5) -> list[SkillMatch]:
        """Recommend skills based on context (e.g., current conversation)."""
        if not self._indexed:
            self.index()

        # Extract keywords from context
        words = re.findall(r'\b[a-zA-Z]{3,}\b', context.lower())
        word_freq: dict[str, int] = {}
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1

        top_words = sorted(word_freq, key=word_freq.get, reverse=True)[:10]
        query = " ".join(top_words)
        return self.search(query, limit)

    def list_all(self, source: str | None = None) -> list[SkillMatch]:
        if not self._indexed:
            self.index()
        if source:
            return [s for s in self._skills if s.source == source]
        return list(self._skills)

    @property
    def count(self) -> int:
        return len(self._skills)


# Module singleton
_engine: SkillSearchEngine | None = None


def get_skill_search() -> SkillSearchEngine:
    global _engine
    if _engine is None:
        _engine = SkillSearchEngine()
    return _engine
