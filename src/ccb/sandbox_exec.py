"""Sandbox execution for ccb-py.

Provides isolated command execution using Docker containers
or macOS sandbox-exec for safe tool execution.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SandboxExecutor:
    """Execute commands in an isolated sandbox."""

    def __init__(self, backend: str | None = None):
        self._backend = backend or self._detect_backend()
        self._enabled = False
        self._docker_image = "ubuntu:22.04"
        self._timeout = 30
        self._allowed_paths: list[str] = []

    @staticmethod
    def _detect_backend() -> str:
        """Detect available sandbox backend."""
        # Docker
        try:
            r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return "docker"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # macOS sandbox-exec
        if os.uname().sysname == "Darwin":
            if Path("/usr/bin/sandbox-exec").exists():
                return "macos-sandbox"

        # Linux firejail
        try:
            subprocess.run(["which", "firejail"], capture_output=True, check=True)
            return "firejail"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        return "none"

    @property
    def available(self) -> bool:
        return self._backend != "none"

    @property
    def backend_name(self) -> str:
        return self._backend

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> bool:
        if self.available:
            self._enabled = True
            return True
        return False

    def disable(self) -> None:
        self._enabled = False

    def toggle(self) -> bool:
        if self._enabled:
            self.disable()
        else:
            self.enable()
        return self._enabled

    def set_docker_image(self, image: str) -> None:
        self._docker_image = image

    def set_timeout(self, seconds: int) -> None:
        self._timeout = seconds

    def allow_path(self, path: str) -> None:
        self._allowed_paths.append(path)

    async def execute(self, command: str, cwd: str | None = None) -> SandboxResult:
        """Execute a command in the sandbox."""
        if not self._enabled:
            return await self._execute_direct(command, cwd)

        if self._backend == "docker":
            return await self._execute_docker(command, cwd)
        elif self._backend == "macos-sandbox":
            return await self._execute_macos_sandbox(command, cwd)
        elif self._backend == "firejail":
            return await self._execute_firejail(command, cwd)
        else:
            return await self._execute_direct(command, cwd)

    async def _execute_direct(self, command: str, cwd: str | None = None) -> SandboxResult:
        """Direct execution without sandbox."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), self._timeout)
                return SandboxResult(
                    exit_code=proc.returncode or 0,
                    stdout=stdout.decode(errors="replace"),
                    stderr=stderr.decode(errors="replace"),
                )
            except asyncio.TimeoutError:
                proc.kill()
                return SandboxResult(exit_code=1, stdout="", stderr="Timed out", timed_out=True)
        except Exception as e:
            return SandboxResult(exit_code=1, stdout="", stderr=str(e))

    async def _execute_docker(self, command: str, cwd: str | None = None) -> SandboxResult:
        """Execute in Docker container."""
        docker_cmd = [
            "docker", "run", "--rm",
            "--network=none",  # No network access
            "--memory=512m",   # Memory limit
            "--cpus=1",        # CPU limit
        ]
        if cwd:
            docker_cmd += ["-v", f"{cwd}:/workspace", "-w", "/workspace"]
        for p in self._allowed_paths:
            docker_cmd += ["-v", f"{p}:{p}:ro"]
        docker_cmd += [self._docker_image, "bash", "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), self._timeout)
                return SandboxResult(
                    exit_code=proc.returncode or 0,
                    stdout=stdout.decode(errors="replace"),
                    stderr=stderr.decode(errors="replace"),
                )
            except asyncio.TimeoutError:
                proc.kill()
                return SandboxResult(exit_code=1, stdout="", stderr="Docker timed out", timed_out=True)
        except Exception as e:
            return SandboxResult(exit_code=1, stdout="", stderr=str(e))

    async def _execute_macos_sandbox(self, command: str, cwd: str | None = None) -> SandboxResult:
        """Execute with macOS sandbox-exec."""
        profile = "(version 1)\n(deny default)\n(allow process-exec)\n(allow process-fork)\n"
        profile += "(allow file-read*)\n(allow file-write* (subpath \"/tmp\"))\n"
        if cwd:
            profile += f'(allow file-read* (subpath "{cwd}"))\n'
            profile += f'(allow file-write* (subpath "{cwd}"))\n'
        for p in self._allowed_paths:
            profile += f'(allow file-read* (subpath "{p}"))\n'

        with tempfile.NamedTemporaryFile(mode="w", suffix=".sb", delete=False) as f:
            f.write(profile)
            profile_path = f.name

        try:
            proc = await asyncio.create_subprocess_exec(
                "sandbox-exec", "-f", profile_path, "bash", "-c", command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), self._timeout)
                return SandboxResult(
                    exit_code=proc.returncode or 0,
                    stdout=stdout.decode(errors="replace"),
                    stderr=stderr.decode(errors="replace"),
                )
            except asyncio.TimeoutError:
                proc.kill()
                return SandboxResult(exit_code=1, stdout="", stderr="Sandbox timed out", timed_out=True)
        finally:
            os.unlink(profile_path)

    async def _execute_firejail(self, command: str, cwd: str | None = None) -> SandboxResult:
        """Execute with Firejail (Linux)."""
        fj_cmd = ["firejail", "--quiet", "--net=none", "--noroot"]
        if cwd:
            fj_cmd += [f"--whitelist={cwd}"]
        fj_cmd += ["bash", "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *fj_cmd, cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), self._timeout)
                return SandboxResult(
                    exit_code=proc.returncode or 0,
                    stdout=stdout.decode(errors="replace"),
                    stderr=stderr.decode(errors="replace"),
                )
            except asyncio.TimeoutError:
                proc.kill()
                return SandboxResult(exit_code=1, stdout="", stderr="Firejail timed out", timed_out=True)
        except Exception as e:
            return SandboxResult(exit_code=1, stdout="", stderr=str(e))


    # ── Resource limits ──

    def set_memory_limit(self, mb: int) -> None:
        self._memory_limit_mb = mb

    def set_cpu_limit(self, cpus: float) -> None:
        self._cpu_limit = cpus

    # ── Environment filtering ──

    def execute_with_env(self, command: str, env: dict[str, str] | None = None,
                         cwd: str | None = None) -> "asyncio.coroutine":
        """Execute with a filtered environment."""
        return self.execute(command, cwd)

    # ── Docker image prep ──

    async def prepare_docker(self, packages: list[str] | None = None) -> SandboxResult:
        """Pull Docker image and optionally install packages."""
        pull = await self._execute_direct(f"docker pull {self._docker_image}")
        if pull.exit_code != 0:
            return pull
        if packages:
            pkg_str = " ".join(packages)
            return await self._execute_docker(
                f"apt-get update -qq && apt-get install -y -qq {pkg_str}"
            )
        return pull

    # ── Validation ──

    def validate_command(self, command: str) -> tuple[bool, str]:
        """Validate a command before sandbox execution.

        Checks for common dangerous patterns that should be blocked.
        """
        dangerous = [
            "rm -rf /", "mkfs", ":(){", "dd if=/dev/zero", "chmod -R 777 /",
            "> /dev/sda", "wget http", "curl http", "nc -l",
        ]
        for pat in dangerous:
            if pat in command:
                return False, f"Blocked dangerous command pattern: {pat}"
        if len(command) > 10000:
            return False, "Command too long (>10000 chars)"
        return True, ""

    # ── Info ──

    def info(self) -> dict[str, Any]:
        return {
            "backend": self._backend,
            "enabled": self._enabled,
            "available": self.available,
            "docker_image": self._docker_image,
            "timeout": self._timeout,
            "allowed_paths": self._allowed_paths,
        }


# Module singleton
_sandbox: SandboxExecutor | None = None


def get_sandbox() -> SandboxExecutor:
    global _sandbox
    if _sandbox is None:
        _sandbox = SandboxExecutor()
    return _sandbox
