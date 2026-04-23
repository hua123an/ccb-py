"""Tests for ccb.sandbox_exec module."""
import pytest

from ccb.sandbox_exec import SandboxExecutor, SandboxResult


class TestSandboxResult:
    def test_basic(self):
        r = SandboxResult(exit_code=0, stdout="hello", stderr="")
        assert r.exit_code == 0
        assert r.timed_out is False

    def test_timeout(self):
        r = SandboxResult(exit_code=1, stdout="", stderr="timed out", timed_out=True)
        assert r.timed_out is True


class TestSandboxDetection:
    def test_detect_backend(self):
        executor = SandboxExecutor()
        assert executor.backend_name in ("docker", "macos-sandbox", "firejail", "none")

    def test_available(self):
        executor = SandboxExecutor(backend="none")
        assert executor.available is False

    def test_enable_disable(self):
        executor = SandboxExecutor(backend="docker")
        assert executor.enable() is True
        assert executor.enabled is True
        executor.disable()
        assert executor.enabled is False

    def test_enable_none_backend(self):
        executor = SandboxExecutor(backend="none")
        assert executor.enable() is False


class TestSandboxConfig:
    def test_set_docker_image(self):
        executor = SandboxExecutor()
        executor.set_docker_image("python:3.12")
        assert executor._docker_image == "python:3.12"

    def test_set_timeout(self):
        executor = SandboxExecutor()
        executor.set_timeout(60)
        assert executor._timeout == 60

    def test_allow_path(self):
        executor = SandboxExecutor()
        executor.allow_path("/tmp/safe")
        assert "/tmp/safe" in executor._allowed_paths

    def test_toggle(self):
        executor = SandboxExecutor(backend="docker")
        executor.toggle()
        assert executor.enabled is True
        executor.toggle()
        assert executor.enabled is False


class TestCommandValidation:
    def test_safe_command(self):
        executor = SandboxExecutor()
        ok, msg = executor.validate_command("ls -la")
        assert ok is True

    def test_dangerous_rm(self):
        executor = SandboxExecutor()
        ok, msg = executor.validate_command("rm -rf /")
        assert ok is False
        assert "dangerous" in msg.lower()

    def test_dangerous_dd(self):
        executor = SandboxExecutor()
        ok, msg = executor.validate_command("dd if=/dev/zero of=/dev/sda")
        assert ok is False

    def test_too_long_command(self):
        executor = SandboxExecutor()
        ok, msg = executor.validate_command("x" * 20000)
        assert ok is False
        assert "too long" in msg.lower()


class TestDirectExecution:
    @pytest.mark.asyncio
    async def test_echo(self):
        executor = SandboxExecutor()
        result = await executor._execute_direct("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_nonexistent_command(self):
        executor = SandboxExecutor()
        result = await executor._execute_direct("nonexistent_command_xyz")
        assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_timeout(self):
        executor = SandboxExecutor()
        executor.set_timeout(1)
        result = await executor._execute_direct("sleep 10")
        assert result.timed_out is True


class TestInfo:
    def test_info(self):
        executor = SandboxExecutor()
        info = executor.info()
        assert "backend" in info
        assert "enabled" in info
        assert "timeout" in info
