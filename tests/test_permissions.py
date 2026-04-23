"""Tests for ccb.permissions module."""
import json
from pathlib import Path

import pytest

from ccb.permissions import (
    _match_pattern,
    _match_content,
    _permission_key,
    _match_workspace_rule,
    SAFE_TOOLS,
    reset_session_permissions,
    set_bypass_all,
    set_tool_filters,
    is_tool_allowed,
    approve_tool,
    record_approval,
    needs_permission,
    add_workspace_rule,
    get_workspace_rules,
    clear_workspace_rules,
)


class TestPatternMatching:
    def test_exact_match(self):
        assert _match_pattern("bash", "bash") is True
        assert _match_pattern("bash", "file_read") is False

    def test_wildcard(self):
        assert _match_pattern("*", "anything") is True

    def test_glob_pattern(self):
        assert _match_pattern("file_*", "file_read") is True
        assert _match_pattern("file_*", "file_write") is True
        assert _match_pattern("file_*", "bash") is False

    def test_content_match_regex(self):
        assert _match_content(r"pip\s+install", "pip install flask") is True
        assert _match_content(r"pip\s+install", "npm install") is False

    def test_content_match_literal_fallback(self):
        assert _match_content("[invalid regex", "[invalid regex in content") is True


class TestPermissionKey:
    def test_bash_key(self):
        key = _permission_key("bash", {"command": "ls -la"})
        assert key == "bash:ls -la"

    def test_file_write_key(self):
        key = _permission_key("file_write", {"file_path": "/tmp/x.py"})
        assert key == "file_write:/tmp/x.py"

    def test_other_tool_key(self):
        key = _permission_key("grep", {"pattern": "foo"})
        assert key == "grep"


class TestSafeTools:
    def test_safe_tools(self):
        assert "file_read" in SAFE_TOOLS
        assert "grep" in SAFE_TOOLS
        assert "bash" not in SAFE_TOOLS
        assert "file_write" not in SAFE_TOOLS


class TestWorkspaceRules:
    def test_match_rule_basic(self):
        rule = {"tool": "bash", "effect": "allow"}
        assert _match_workspace_rule(rule, "bash", {}) is True
        assert _match_workspace_rule(rule, "file_read", {}) is False

    def test_match_rule_with_prefix(self):
        rule = {"tool": "bash", "effect": "allow", "command_prefix": "npm"}
        assert _match_workspace_rule(rule, "bash", {"command": "npm install"}) is True
        assert _match_workspace_rule(rule, "bash", {"command": "pip install"}) is False

    def test_match_rule_with_path(self):
        rule = {"tool": "file_write", "effect": "allow", "path": "/tmp/"}
        assert _match_workspace_rule(rule, "file_write", {"file_path": "/tmp/x.py"}) is True
        assert _match_workspace_rule(rule, "file_write", {"file_path": "/etc/x.py"}) is False


class TestSessionPermissions:
    def setup_method(self):
        reset_session_permissions()
        set_tool_filters(None, None)
        set_bypass_all(False)

    def test_approve_once(self):
        record_approval("bash", {"command": "ls"}, "allow_once")
        # Now that specific command should be approved

    def test_approve_session(self):
        record_approval("bash", {"command": "ls"}, "allow_session")
        # All bash should be approved for session

    def test_bypass_all(self):
        set_bypass_all(True)
        assert needs_permission("bash", {"command": "rm -rf /"}) is False

    def test_safe_tool_no_permission(self):
        assert needs_permission("file_read", {"file_path": "/tmp/x"}) is False


class TestToolFilters:
    def setup_method(self):
        reset_session_permissions()
        set_tool_filters(None, None)

    def test_deny_filter(self):
        set_tool_filters(denied=["bash", "file_write"])
        assert is_tool_allowed("bash") is False
        assert is_tool_allowed("file_read") is True

    def test_allow_filter(self):
        set_tool_filters(allowed=["file_read", "grep"])
        assert is_tool_allowed("file_read") is True
        assert is_tool_allowed("bash") is False

    def test_glob_filter(self):
        set_tool_filters(allowed=["file_*"])
        assert is_tool_allowed("file_read") is True
        assert is_tool_allowed("file_write") is True
        assert is_tool_allowed("bash") is False

    def teardown_method(self):
        set_tool_filters(None, None)
