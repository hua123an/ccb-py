"""Permission system - controls which tool calls need user approval.

Supports 5-level approval choices:
  allow_once      – allow this specific call, don't remember
  allow_session   – allow this tool/command for the rest of the session
  allow_workspace – persist an allow rule to ~/.claude/approvals/<hash>.json
  deny_once       – deny this specific call
  deny_workspace  – persist a deny rule to ~/.claude/approvals/<hash>.json
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from ccb.config import claude_dir, get_permission_mode, get_settings

# ── Types ──
ApprovalChoice = Literal[
    "allow_once", "allow_session", "allow_workspace",
    "deny_once", "deny_workspace",
]

# ── Read-only tools that never need permission ──
SAFE_TOOLS = frozenset({
    "file_read", "grep", "glob", "agent",
    "ask_user_question", "task_stop", "task_output",
    "list_mcp_resources", "read_mcp_resource",
    "enter_plan_mode", "exit_plan_mode",
})

# ── Runtime state ──
_session_approved: set[str] = set()
_session_denied: set[str] = set()
_bypass_all = False
_allowed_tools: set[str] | None = None
_denied_tools: set[str] | None = None


# ---------------------------------------------------------------------------
# Workspace-persistent approval rules
# ---------------------------------------------------------------------------

def _approvals_dir() -> Path:
    d = claude_dir() / "approvals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _workspace_key(cwd: str) -> str:
    return hashlib.sha256(cwd.encode()).hexdigest()[:16]


def _load_workspace_rules(cwd: str) -> list[dict[str, Any]]:
    path = _approvals_dir() / f"{_workspace_key(cwd)}.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_workspace_rules(cwd: str, rules: list[dict[str, Any]]) -> None:
    path = _approvals_dir() / f"{_workspace_key(cwd)}.json"
    path.write_text(json.dumps(rules, indent=2, ensure_ascii=False))


def add_workspace_rule(cwd: str, tool_name: str, effect: str,
                       command_prefix: str = "", file_path: str = "") -> None:
    """Add a persistent workspace-level approval/deny rule."""
    rules = _load_workspace_rules(cwd)
    rule: dict[str, Any] = {"tool": tool_name, "effect": effect}
    if command_prefix:
        rule["command_prefix"] = command_prefix
    if file_path:
        rule["path"] = file_path
    # Avoid duplicates
    if rule not in rules:
        rules.append(rule)
    _save_workspace_rules(cwd, rules)


def get_workspace_rules(cwd: str) -> list[dict[str, Any]]:
    """Get all persistent workspace rules (for display)."""
    return _load_workspace_rules(cwd)


def clear_workspace_rules(cwd: str) -> None:
    """Clear all persistent workspace rules."""
    path = _approvals_dir() / f"{_workspace_key(cwd)}.json"
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reset_session_permissions() -> None:
    global _bypass_all
    _session_approved.clear()
    _session_denied.clear()
    _bypass_all = False


def set_bypass_all(bypass: bool) -> None:
    global _bypass_all
    _bypass_all = bypass


def set_tool_filters(allowed: list[str] | None = None, denied: list[str] | None = None) -> None:
    """Set allowed/denied tool name filters (supports glob patterns)."""
    global _allowed_tools, _denied_tools
    _allowed_tools = set(allowed) if allowed else None
    _denied_tools = set(denied) if denied else None


def is_tool_allowed(tool_name: str) -> bool:
    """Check if a tool is allowed by allow/deny rules from settings + CLI."""
    settings = get_settings()
    deny_rules = settings.get("permissions", {}).get("deny", [])
    for rule in deny_rules:
        pattern = rule if isinstance(rule, str) else rule.get("tool", "")
        if _match_pattern(pattern, tool_name):
            return False

    if _denied_tools:
        for pat in _denied_tools:
            if _match_pattern(pat, tool_name):
                return False

    if _allowed_tools:
        for pat in _allowed_tools:
            if _match_pattern(pat, tool_name):
                return True
        return False

    return True


def needs_permission(tool_name: str, input_data: dict[str, Any],
                     cwd: str = "") -> bool:
    """Check if a tool call needs user permission.

    Returns False if auto-approved by any rule layer:
      1. bypass mode
      2. safe tools
      3. settings.json allow rules
      4. workspace persistent rules
      5. session-level approvals
    """
    if _bypass_all:
        return False
    if get_permission_mode() == "bypassPermissions":
        return False
    if tool_name in SAFE_TOOLS:
        return False

    # Parallel subagents must not be able to open interactive prompts in the
    # parent REPL — there's no safe way to route N concurrent prompts to one
    # user. We auto-approve tool calls that originate from within an agent.
    # Destructive actions are still gated by the standard deny-list and any
    # settings.json deny rules (checked later by is_auto_denied).
    try:
        from ccb.agent_context import is_inside_agent
        if is_inside_agent():
            return False
    except Exception:
        pass

    # Check settings.json allow rules
    settings = get_settings()
    allow_rules = settings.get("permissions", {}).get("allow", [])
    for rule in allow_rules:
        if isinstance(rule, str):
            if _match_pattern(rule, tool_name):
                return False
        elif isinstance(rule, dict):
            pattern = rule.get("tool", "")
            if _match_pattern(pattern, tool_name):
                content = rule.get("content")
                if content is None:
                    return False
                if tool_name == "bash":
                    cmd = input_data.get("command", "")
                    if _match_content(content, cmd):
                        return False

    # Check workspace persistent rules
    if cwd:
        ws_rules = _load_workspace_rules(cwd)
        for rule in ws_rules:
            if _match_workspace_rule(rule, tool_name, input_data):
                if rule.get("effect") == "allow":
                    return False
                if rule.get("effect") == "deny":
                    return True  # force deny → still "needs permission" but will be auto-denied

    # Check session-level
    key = _permission_key(tool_name, input_data)
    if key in _session_approved or tool_name in _session_approved:
        return False
    if key in _session_denied or tool_name in _session_denied:
        return True  # needs permission but will be auto-denied

    return True


def is_auto_denied(tool_name: str, input_data: dict[str, Any],
                   cwd: str = "") -> bool:
    """Check if a tool call is auto-denied by persistent or session rules."""
    # Workspace deny rules
    if cwd:
        for rule in _load_workspace_rules(cwd):
            if _match_workspace_rule(rule, tool_name, input_data):
                if rule.get("effect") == "deny":
                    return True
    # Session deny
    key = _permission_key(tool_name, input_data)
    return key in _session_denied or tool_name in _session_denied


def record_approval(tool_name: str, input_data: dict[str, Any],
                    choice: ApprovalChoice, cwd: str = "") -> None:
    """Record an approval decision at the chosen level."""
    if choice == "allow_once":
        key = _permission_key(tool_name, input_data)
        _session_approved.add(key)

    elif choice == "allow_session":
        _session_approved.add(tool_name)

    elif choice == "allow_workspace":
        _session_approved.add(tool_name)
        if cwd:
            cmd_prefix = ""
            fp = ""
            if tool_name == "bash":
                cmd = input_data.get("command", "").strip()
                # Store first word as prefix for bash commands
                cmd_prefix = cmd.split()[0] if cmd else ""
            elif tool_name in ("file_write", "file_edit"):
                fp = input_data.get("file_path", "")
            add_workspace_rule(cwd, tool_name, "allow",
                               command_prefix=cmd_prefix, file_path=fp)

    elif choice == "deny_once":
        key = _permission_key(tool_name, input_data)
        _session_denied.add(key)

    elif choice == "deny_workspace":
        _session_denied.add(tool_name)
        if cwd:
            add_workspace_rule(cwd, tool_name, "deny")


def approve_tool(tool_name: str, input_data: dict[str, Any], always: bool = False) -> None:
    """Legacy API — record that a tool call was approved (session level)."""
    if always:
        _session_approved.add(tool_name)
    else:
        key = _permission_key(tool_name, input_data)
        _session_approved.add(key)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _permission_key(tool_name: str, input_data: dict[str, Any]) -> str:
    """Generate a permission key for deduplication."""
    if tool_name == "bash":
        return f"bash:{input_data.get('command', '')}"
    if tool_name in ("file_write", "file_edit"):
        return f"{tool_name}:{input_data.get('file_path', '')}"
    return tool_name


def _match_pattern(pattern: str, tool_name: str) -> bool:
    if pattern == tool_name or pattern == "*":
        return True
    return fnmatch.fnmatch(tool_name, pattern)


def _match_content(pattern: str, content: str) -> bool:
    try:
        return bool(re.search(pattern, content))
    except re.error:
        return pattern in content


def _match_workspace_rule(rule: dict[str, Any], tool_name: str,
                          input_data: dict[str, Any]) -> bool:
    """Check if a workspace rule matches the current tool call."""
    if not _match_pattern(rule.get("tool", ""), tool_name):
        return False
    # Command prefix match for bash
    cmd_prefix = rule.get("command_prefix", "")
    if cmd_prefix and tool_name == "bash":
        cmd = input_data.get("command", "").strip()
        if not cmd.startswith(cmd_prefix):
            return False
    # Path match for file tools
    rule_path = rule.get("path", "")
    if rule_path and tool_name in ("file_write", "file_edit"):
        fp = input_data.get("file_path", "")
        if rule_path.endswith("/"):
            if not fp.startswith(rule_path) and fp != rule_path.rstrip("/"):
                return False
        elif fp != rule_path:
            return False
    return True
