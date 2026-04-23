"""MCP server configuration validation.

Validates MCP config from .claude.json and .mcp.json before connection,
catching misconfiguration early with helpful error messages.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationError:
    server_name: str
    field: str
    message: str
    severity: str = "error"  # "error", "warning"


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    def add_error(self, server: str, field: str, msg: str) -> None:
        self.errors.append(ValidationError(server, field, msg, "error"))
        self.valid = False

    def add_warning(self, server: str, field: str, msg: str) -> None:
        self.warnings.append(ValidationError(server, field, msg, "warning"))

    def format(self) -> str:
        lines = []
        for e in self.errors:
            lines.append(f"  ✗ [{e.server_name}] {e.field}: {e.message}")
        for w in self.warnings:
            lines.append(f"  ⚠ [{w.server_name}] {w.field}: {w.message}")
        return "\n".join(lines) if lines else "  ✓ All MCP configs valid"


def validate_server_config(name: str, cfg: dict[str, Any]) -> ValidationResult:
    """Validate a single MCP server configuration."""
    result = ValidationResult(valid=True)

    server_type = cfg.get("type", "stdio")

    if server_type == "stdio":
        # Must have command
        command = cfg.get("command", "")
        if not command:
            result.add_error(name, "command", "Missing 'command' field for stdio server")
            return result

        # Check if command exists
        resolved = shutil.which(command)
        if not resolved:
            # Check with full path
            if not os.path.isfile(command):
                result.add_warning(name, "command",
                    f"Command '{command}' not found in PATH. "
                    f"It may work if installed in the server's env.")

        # Validate args is a list
        args = cfg.get("args", [])
        if not isinstance(args, list):
            result.add_error(name, "args", "args must be a list of strings")
        elif not all(isinstance(a, str) for a in args):
            result.add_error(name, "args", "All args must be strings")

        # Validate env is a dict of strings
        env = cfg.get("env", {})
        if not isinstance(env, dict):
            result.add_error(name, "env", "env must be a dict of string key-value pairs")
        else:
            for k, v in env.items():
                if not isinstance(v, str):
                    result.add_warning(name, f"env.{k}", f"Value should be string, got {type(v).__name__}")

    elif server_type in ("http", "sse"):
        url = cfg.get("url", "")
        if not url:
            result.add_error(name, "url", "Missing 'url' field for HTTP/SSE server")
        elif not url.startswith(("http://", "https://")):
            result.add_error(name, "url", f"URL must start with http:// or https://, got: {url[:30]}")

        # Validate headers
        headers = cfg.get("headers", {})
        if not isinstance(headers, dict):
            result.add_error(name, "headers", "headers must be a dict")

    else:
        result.add_error(name, "type", f"Unknown server type: {server_type}. Use 'stdio' or 'http'.")

    # Common validations
    if not name:
        result.add_error(name or "(empty)", "name", "Server name cannot be empty")
    elif not name.replace("-", "").replace("_", "").isalnum():
        result.add_warning(name, "name", "Server name should be alphanumeric with hyphens/underscores")

    return result


def validate_all_configs(configs: dict[str, dict[str, Any]]) -> ValidationResult:
    """Validate all MCP server configurations."""
    combined = ValidationResult(valid=True)
    for name, cfg in configs.items():
        result = validate_server_config(name, cfg)
        combined.errors.extend(result.errors)
        combined.warnings.extend(result.warnings)
        if not result.valid:
            combined.valid = False

    # Check for duplicate server names (shouldn't happen with dict, but defensive)
    # Check for port conflicts in HTTP servers
    ports: dict[int, str] = {}
    for name, cfg in configs.items():
        url = cfg.get("url", "")
        if url:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                if port in ports:
                    combined.add_warning(name, "url",
                        f"Port {port} also used by server '{ports[port]}'")
                ports[port] = name
            except Exception:
                pass

    return combined
