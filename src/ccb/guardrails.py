"""Guardrails - input/output validation for agent safety.

Provides configurable rules for validating agent inputs and outputs,
inspired by OpenAI Agents SDK guardrails system.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class GuardrailResult:
    """Result of a guardrail check."""
    passed: bool
    rule_name: str
    message: str = ""
    severity: str = "block"  # block, warn, log


@dataclass
class InputGuardrail:
    """Validates agent input before execution."""
    name: str
    description: str = ""
    check: Callable[[str], GuardrailResult] | None = None
    enabled: bool = True


@dataclass
class OutputGuardrail:
    """Validates agent output after execution."""
    name: str
    description: str = ""
    check: Callable[[str], GuardrailResult] | None = None
    enabled: bool = True


class GuardrailRunner:
    """Run guardrail checks on inputs and outputs."""

    def __init__(self) -> None:
        self._input_rules: list[InputGuardrail] = []
        self._output_rules: list[OutputGuardrail] = []
        self._setup_defaults()

    def _setup_defaults(self) -> None:
        """Register default safety guardrails."""
        self.add_input(InputGuardrail(
            name="no_secrets",
            description="Block prompts that look like they're asking for secrets/credentials",
            check=self._check_no_secrets,
        ))
        self.add_input(InputGuardrail(
            name="no_injection",
            description="Block potential prompt injection patterns",
            check=self._check_injection,
        ))
        self.add_output(OutputGuardrail(
            name="no_credentials",
            description="Block output containing API keys or passwords",
            check=self._check_output_credentials,
        ))

    def add_input(self, rule: InputGuardrail) -> None:
        self._input_rules.append(rule)

    def add_output(self, rule: OutputGuardrail) -> None:
        self._output_rules.append(rule)

    def remove_input(self, name: str) -> bool:
        before = len(self._input_rules)
        self._input_rules = [r for r in self._input_rules if r.name != name]
        return len(self._input_rules) < before

    def remove_output(self, name: str) -> bool:
        before = len(self._output_rules)
        self._output_rules = [r for r in self._output_rules if r.name != name]
        return len(self._output_rules) < before

    def check_input(self, text: str) -> list[GuardrailResult]:
        """Run all input guardrails. Returns list of failures."""
        results = []
        for rule in self._input_rules:
            if not rule.enabled or not rule.check:
                continue
            result = rule.check(text)
            if not result.passed:
                results.append(result)
        return results

    def check_output(self, text: str) -> list[GuardrailResult]:
        """Run all output guardrails. Returns list of failures."""
        results = []
        for rule in self._output_rules:
            if not rule.enabled or not rule.check:
                continue
            result = rule.check(text)
            if not result.passed:
                results.append(result)
        return results

    @staticmethod
    def _check_no_secrets(text: str) -> GuardrailResult:
        """Detect prompts asking for credential extraction."""
        patterns = [
            r"(?:give|show|tell|send|dump|extract|leak)\s+(?:me\s+)?(?:the\s+)?(?:api[_\s]?key|password|secret|token|credential)",
            r"(?:what(?:'s| is| are))\s+(?:the\s+)?(?:api[_\s]?key|password|secret|token)",
        ]
        text_lower = text.lower()
        for pat in patterns:
            if re.search(pat, text_lower):
                return GuardrailResult(
                    passed=False,
                    rule_name="no_secrets",
                    message="Prompt appears to request sensitive credentials",
                    severity="warn",
                )
        return GuardrailResult(passed=True, rule_name="no_secrets")

    @staticmethod
    def _check_injection(text: str) -> GuardrailResult:
        """Detect potential prompt injection attempts."""
        injection_patterns = [
            r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|prompts|rules)",
            r"you\s+are\s+now\s+(?:a|an|the)\s+",
            r"system\s*:\s*",
            r"<\|im_start\|>",
            r"\[INST\]",
            r"###\s*(?:SYSTEM|INSTRUCTION)",
        ]
        text_lower = text.lower()
        for pat in injection_patterns:
            if re.search(pat, text_lower):
                return GuardrailResult(
                    passed=False,
                    rule_name="no_injection",
                    message=f"Potential prompt injection detected (pattern: {pat})",
                    severity="block",
                )
        return GuardrailResult(passed=True, rule_name="no_injection")

    @staticmethod
    def _check_output_credentials(text: str) -> GuardrailResult:
        """Detect credentials in output."""
        patterns = [
            (r"(?:sk-[a-zA-Z0-9]{20,})", "OpenAI API key"),
            (r"(?:AKIA[0-9A-Z]{16})", "AWS access key"),
            (r"(?:ghp_[a-zA-Z0-9]{36})", "GitHub token"),
            (r"(?:xoxb-[0-9]+-[a-zA-Z0-9]+)", "Slack token"),
        ]
        for pat, name in patterns:
            if re.search(pat, text):
                return GuardrailResult(
                    passed=False,
                    rule_name="no_credentials",
                    message=f"Output contains what appears to be a {name}",
                    severity="warn",
                )
        return GuardrailResult(passed=True, rule_name="no_credentials")

    def list_rules(self) -> dict[str, list[dict[str, Any]]]:
        """List all registered guardrail rules."""
        return {
            "input": [
                {
                    "name": r.name,
                    "description": r.description,
                    "enabled": r.enabled,
                }
                for r in self._input_rules
            ],
            "output": [
                {
                    "name": r.name,
                    "description": r.description,
                    "enabled": r.enabled,
                }
                for r in self._output_rules
            ],
        }


# Module singleton
_runner: GuardrailRunner | None = None


def get_guardrails() -> GuardrailRunner:
    global _runner
    if _runner is None:
        _runner = GuardrailRunner()
    return _runner
