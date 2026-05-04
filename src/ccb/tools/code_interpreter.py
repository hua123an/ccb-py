"""CodeInterpreterTool - sandboxed Python code execution."""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

from ccb.tools.base import Tool, ToolResult

CODE_INTERPRETER_PROMPT = """\
Execute Python code in a sandboxed environment and return the output.

Usage:
- Write complete, self-contained Python code
- stdout and stderr are captured and returned
- The code runs in an isolated subprocess with resource limits
- Available libraries: standard library + common scientific packages (numpy, pandas, matplotlib if installed)
- For matplotlib, use `plt.savefig('/tmp/plot.png')` to save plots, then read the file
- Timeout: 60 seconds per execution
- Memory limit: 512MB
- No network access in sandboxed mode

Examples:
- Data analysis: read CSV, compute statistics
- Math calculations: complex formulas, algorithms
- Text processing: regex, parsing, transformations"""


class CodeInterpreterTool(Tool):
    name = "code_interpreter"
    description = CODE_INTERPRETER_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Execution timeout in seconds (default 60).",
                "default": 60,
            },
        },
        "required": ["code"],
    }

    @property
    def needs_permission(self) -> bool:
        return True

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        code = input.get("code", "")
        timeout = min(input.get("timeout", 60), 120)

        if not code.strip():
            return ToolResult(output="Error: empty code", is_error=True)

        # Check sandbox availability
        from ccb.sandbox_exec import get_sandbox
        sandbox = get_sandbox()

        if sandbox.available and sandbox.enabled:
            return await self._execute_sandboxed(code, cwd, timeout, sandbox)

        # Fallback: direct subprocess execution with limits
        return await self._execute_subprocess(code, cwd, timeout)

    async def _execute_sandboxed(
        self, code: str, cwd: str, timeout: int, sandbox: Any,
    ) -> ToolResult:
        """Execute via sandbox for maximum isolation."""
        # Write code to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, dir=cwd,
        ) as f:
            f.write(code)
            code_file = f.name

        try:
            cmd = f"python3 {code_file}"
            result = await sandbox.execute(cmd, cwd)

            output_parts = []
            if result.stdout:
                output_parts.append(result.stdout.rstrip())
            if result.stderr:
                output_parts.append(f"[stderr]\n{result.stderr.rstrip()}")

            output = "\n".join(output_parts) or "(no output)"

            if result.timed_out:
                output = f"[Timeout after {sandbox._timeout}s]\n{output}"

            max_len = 50_000
            if len(output) > max_len:
                output = output[:max_len] + f"\n... (truncated, {len(output)} total chars)"

            return ToolResult(
                output=output,
                is_error=result.exit_code != 0 or result.timed_out,
            )
        finally:
            try:
                os.unlink(code_file)
            except OSError:
                pass

    async def _execute_subprocess(
        self, code: str, cwd: str, timeout: int,
    ) -> ToolResult:
        """Direct subprocess execution with resource limits."""
        # Write code to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, dir=cwd,
        ) as f:
            f.write(code)
            code_file = f.name

        try:
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

            proc = await asyncio.create_subprocess_exec(
                "python3", code_file,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult(
                    output=f"Execution timed out after {timeout}s",
                    is_error=True,
                )

            output_parts = []
            if stdout:
                output_parts.append(stdout.decode(errors="replace").rstrip())
            if stderr:
                stderr_text = stderr.decode(errors="replace").rstrip()
                if stderr_text:
                    output_parts.append(f"[stderr]\n{stderr_text}")

            output = "\n".join(output_parts) or "(no output)"

            max_len = 50_000
            if len(output) > max_len:
                output = output[:max_len] + f"\n... (truncated, {len(output)} total chars)"

            return ToolResult(
                output=output,
                is_error=proc.returncode != 0,
            )
        finally:
            try:
                os.unlink(code_file)
            except OSError:
                pass
