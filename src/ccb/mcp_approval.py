"""MCP tool approval workflow.

Provides configurable approval rules for MCP tool execution:
- auto_approve: execute without asking
- always_ask: always prompt user for approval
- per_tool: tool-specific rules

Inspired by OpenAI Agents SDK MCP approval system.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ApprovalMode(str, Enum):
    AUTO = "auto"
    ASK = "ask"
    DENY = "deny"


@dataclass
class ToolApprovalRule:
    """Approval rule for a specific tool."""
    tool_name: str
    mode: ApprovalMode = ApprovalMode.ASK
    server: str = ""
    reason: str = ""


class McpApprovalManager:
    """Manage MCP tool approval policies."""

    def __init__(self) -> None:
        self._rules: dict[str, ToolApprovalRule] = {}
        self._default_mode: ApprovalMode = ApprovalMode.ASK
        self._auto_approve_servers: set[str] = set()
        self._session_approvals: dict[str, bool] = {}  # tool_name -> approved this session

    @property
    def default_mode(self) -> ApprovalMode:
        return self._default_mode

    @default_mode.setter
    def default_mode(self, mode: ApprovalMode) -> None:
        self._default_mode = mode

    def set_rule(self, rule: ToolApprovalRule) -> None:
        """Set approval rule for a tool."""
        key = self._rule_key(rule.tool_name, rule.server)
        self._rules[key] = rule

    def remove_rule(self, tool_name: str, server: str = "") -> bool:
        key = self._rule_key(tool_name, server)
        return self._rules.pop(key, None) is not None

    def auto_approve_server(self, server: str) -> None:
        """Auto-approve all tools from a server."""
        self._auto_approve_servers.add(server)

    def revoke_server(self, server: str) -> None:
        """Revoke auto-approve for a server."""
        self._auto_approve_servers.discard(server)

    def check_approval(
        self,
        tool_name: str,
        server: str = "",
        tool_input: dict[str, Any] | None = None,
    ) -> tuple[ApprovalMode, str]:
        """Check if a tool call should be approved.

        Returns (mode, reason).
        """
        # Check session approvals
        session_key = self._rule_key(tool_name, server)
        if session_key in self._session_approvals:
            if self._session_approvals[session_key]:
                return ApprovalMode.AUTO, "Previously approved this session"
            return ApprovalMode.DENY, "Previously denied this session"

        # Check server auto-approve
        if server in self._auto_approve_servers:
            return ApprovalMode.AUTO, f"Server '{server}' is auto-approved"

        # Check per-tool rules
        key = self._rule_key(tool_name, server)
        if key in self._rules:
            rule = self._rules[key]
            return rule.mode, rule.reason

        # Check tool-only rules (no server specified)
        if tool_name in self._rules:
            rule = self._rules[tool_name]
            return rule.mode, rule.reason

        # Default
        return self._default_mode, f"Default policy: {self._default_mode.value}"

    def record_approval(self, tool_name: str, server: str = "", approved: bool = True) -> None:
        """Record that a tool was approved/denied this session."""
        key = self._rule_key(tool_name, server)
        self._session_approvals[key] = approved

    def clear_session_approvals(self) -> None:
        self._session_approvals.clear()

    @staticmethod
    def _rule_key(tool_name: str, server: str = "") -> str:
        if server:
            return f"{server}::{tool_name}"
        return tool_name

    def list_rules(self) -> dict[str, Any]:
        """List all rules and current state."""
        return {
            "default_mode": self._default_mode.value,
            "auto_approve_servers": sorted(self._auto_approve_servers),
            "rules": [
                {
                    "tool": r.tool_name,
                    "server": r.server,
                    "mode": r.mode.value,
                    "reason": r.reason,
                }
                for r in self._rules.values()
            ],
            "session_approvals": dict(self._session_approvals),
        }

    def save(self, path: Path | None = None) -> None:
        """Save rules to disk."""
        p = path or (Path.home() / ".claude" / "mcp_approval.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "default_mode": self._default_mode.value,
            "auto_approve_servers": sorted(self._auto_approve_servers),
            "rules": {
                k: {"tool": r.tool_name, "server": r.server, "mode": r.mode.value, "reason": r.reason}
                for k, r in self._rules.items()
            },
        }
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def load(self, path: Path | None = None) -> None:
        """Load rules from disk."""
        p = path or (Path.home() / ".claude" / "mcp_approval.json")
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text())
            self._default_mode = ApprovalMode(data.get("default_mode", "ask"))
            self._auto_approve_servers = set(data.get("auto_approve_servers", []))
            for k, r in data.get("rules", {}).items():
                self._rules[k] = ToolApprovalRule(
                    tool_name=r["tool"],
                    server=r.get("server", ""),
                    mode=ApprovalMode(r.get("mode", "ask")),
                    reason=r.get("reason", ""),
                )
        except (json.JSONDecodeError, OSError, KeyError):
            pass


# Module singleton
_manager: McpApprovalManager | None = None


def get_approval_manager() -> McpApprovalManager:
    global _manager
    if _manager is None:
        _manager = McpApprovalManager()
        _manager.load()
    return _manager
