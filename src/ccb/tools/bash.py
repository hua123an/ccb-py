"""Bash tool - execute shell commands."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import BASH_PROMPT


class BashTool(Tool):
    name = "bash"
    description = BASH_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120).",
                "default": 120,
            },
        },
        "required": ["command"],
    }

    @property
    def needs_permission(self) -> bool:
        return True

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        command = input.get("command", "")
        timeout = input.get("timeout", 120)

        if not command.strip():
            return ToolResult(output="Error: empty command", is_error=True)

        # Check if sandbox mode is enabled via global state
        from ccb.state import get_state
        state = get_state()
        sandbox_enabled = state.get("sandbox_mode", False) if state else False

        if sandbox_enabled:
            return await self._execute_sandbox(command, cwd, timeout)
        else:
            return await self._execute_direct(command, cwd, timeout)

    async def _execute_sandbox(self, command: str, cwd: str, timeout: int) -> ToolResult:
        """Execute via SandboxExecutor for isolation."""
        from ccb.sandbox_exec import get_sandbox

        sandbox = get_sandbox()
        if not sandbox.enabled:
            # Auto-enable if available
            if sandbox.available:
                sandbox.enable()
            else:
                return ToolResult(
                    output="Sandbox mode enabled but no backend available (docker/macos-sandbox/firejail)",
                    is_error=True,
                )

        # Validate command before execution
        ok, msg = sandbox.validate_command(command)
        if not ok:
            return ToolResult(output=f"Sandbox validation failed: {msg}", is_error=True)

        # Execute in sandbox with timeout
        result = await sandbox.execute(command, cwd)

        output_parts = []
        if result.stdout:
            output_parts.append(result.stdout)
        if result.stderr:
            output_parts.append(result.stderr)
        output = "\n".join(output_parts).rstrip()

        if result.timed_out:
            output = f"[Sandbox timeout after {sandbox._timeout}s]\n{output}"

        # Add sandbox indicator to output
        prefix = f"[{sandbox.backend_name}] "
        if result.exit_code != 0:
            output = f"{prefix}Exit code: {result.exit_code}\n{output}"
        else:
            output = f"{prefix}{output}" if output else f"{prefix}(no output)"

        # Truncate very long output
        max_len = 100_000
        if len(output) > max_len:
            output = output[:max_len] + f"\n... (truncated, {len(output)} total chars)"

        return ToolResult(output=output, is_error=result.exit_code != 0 or result.timed_out)

    async def _execute_direct(self, command: str, cwd: str, timeout: int) -> ToolResult:
        """Direct execution without sandbox (original behavior)."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={**os.environ, "PAGER": "cat"},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output_parts = []
            if stdout:
                output_parts.append(stdout.decode(errors="replace"))
            if stderr:
                output_parts.append(stderr.decode(errors="replace"))
            output = "\n".join(output_parts).rstrip()

            if proc.returncode != 0:
                output = f"Exit code: {proc.returncode}\n{output}"

            # Truncate very long output
            max_len = 100_000
            if len(output) > max_len:
                output = output[:max_len] + f"\n... (truncated, {len(output)} total chars)"

            return ToolResult(output=output or "(no output)", is_error=proc.returncode != 0)

        except asyncio.TimeoutError:
            return ToolResult(output=f"Command timed out after {timeout}s", is_error=True)
        except Exception as e:
            return ToolResult(output=f"Error: {e}", is_error=True)
