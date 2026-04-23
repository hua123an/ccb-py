"""Remote/SSH support for ccb-py.

Enables connecting to remote development environments via SSH
and executing operations on remote machines.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RemoteHost:
    name: str
    host: str
    user: str = ""
    port: int = 22
    key_file: str = ""
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)

    @property
    def ssh_target(self) -> str:
        target = self.host
        if self.user:
            target = f"{self.user}@{target}"
        return target

    def ssh_args(self) -> list[str]:
        args = ["ssh"]
        if self.port != 22:
            args += ["-p", str(self.port)]
        if self.key_file:
            args += ["-i", self.key_file]
        args.append(self.ssh_target)
        return args


class RemoteManager:
    """Manage remote connections and operations."""

    def __init__(self) -> None:
        self._hosts: dict[str, RemoteHost] = {}
        self._active: str | None = None
        self._config_file = Path.home() / ".claude" / "remote_hosts.json"
        self._load_config()

    def _load_config(self) -> None:
        if not self._config_file.exists():
            return
        try:
            data = json.loads(self._config_file.read_text())
            for name, entry in data.items():
                self._hosts[name] = RemoteHost(name=name, **entry)
        except (json.JSONDecodeError, OSError):
            pass

    def _save_config(self) -> None:
        data = {}
        for name, host in self._hosts.items():
            data[name] = {
                "host": host.host,
                "user": host.user,
                "port": host.port,
                "key_file": host.key_file,
                "cwd": host.cwd,
            }
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        self._config_file.write_text(json.dumps(data, indent=2))

    def add_host(self, name: str, host: str, user: str = "", port: int = 22,
                 key_file: str = "", cwd: str = "") -> RemoteHost:
        rh = RemoteHost(name=name, host=host, user=user, port=port,
                        key_file=key_file, cwd=cwd)
        self._hosts[name] = rh
        self._save_config()
        return rh

    def remove_host(self, name: str) -> bool:
        if name in self._hosts:
            del self._hosts[name]
            self._save_config()
            if self._active == name:
                self._active = None
            return True
        return False

    def list_hosts(self) -> list[RemoteHost]:
        return list(self._hosts.values())

    def get_host(self, name: str) -> RemoteHost | None:
        return self._hosts.get(name)

    def connect(self, name: str) -> bool:
        if name not in self._hosts:
            return False
        self._active = name
        return True

    def disconnect(self) -> None:
        self._active = None

    @property
    def active_host(self) -> RemoteHost | None:
        return self._hosts.get(self._active, None) if self._active else None

    def test_connection(self, name: str) -> tuple[bool, str]:
        host = self._hosts.get(name)
        if not host:
            return False, f"Unknown host: {name}"
        args = host.ssh_args() + ["-o", "ConnectTimeout=5", "echo", "ok"]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=10)
            return r.returncode == 0, r.stdout.strip() or r.stderr.strip()
        except FileNotFoundError:
            return False, "ssh not found"
        except subprocess.TimeoutExpired:
            return False, "Connection timed out"

    def run_remote(self, command: str, name: str | None = None) -> tuple[int, str, str]:
        """Execute a command on a remote host."""
        host_name = name or self._active
        if not host_name:
            return 1, "", "No active remote host"
        host = self._hosts.get(host_name)
        if not host:
            return 1, "", f"Unknown host: {host_name}"

        ssh_cmd = host.ssh_args()
        if host.cwd:
            command = f"cd {host.cwd} && {command}"
        ssh_cmd.append(command)

        try:
            r = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=60)
            return r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            return 1, "", "Command timed out"

    async def run_remote_async(self, command: str, name: str | None = None) -> tuple[int, str, str]:
        """Async version of run_remote."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.run_remote, command, name)

    # ── SSH tunnel ──

    def create_tunnel(self, name: str, local_port: int, remote_port: int) -> subprocess.Popen | None:
        host = self._hosts.get(name)
        if not host:
            return None
        args = host.ssh_args()
        args[0:0] = []  # keep ssh at front
        tunnel_args = [
            "ssh", "-N", "-L", f"{local_port}:localhost:{remote_port}",
        ]
        if host.port != 22:
            tunnel_args += ["-p", str(host.port)]
        if host.key_file:
            tunnel_args += ["-i", host.key_file]
        tunnel_args.append(host.ssh_target)

        try:
            return subprocess.Popen(tunnel_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception:
            return None

    # ── Parse SSH config ──

    @staticmethod
    def parse_ssh_config() -> list[dict[str, str]]:
        """Parse ~/.ssh/config for known hosts."""
        config_file = Path.home() / ".ssh" / "config"
        if not config_file.exists():
            return []
        hosts = []
        current: dict[str, str] = {}
        try:
            for line in config_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("Host ") and not line.startswith("Host *"):
                    if current:
                        hosts.append(current)
                    current = {"name": line.split(maxsplit=1)[1]}
                elif line.startswith("HostName"):
                    current["host"] = line.split(maxsplit=1)[1]
                elif line.startswith("User"):
                    current["user"] = line.split(maxsplit=1)[1]
                elif line.startswith("Port"):
                    current["port"] = line.split(maxsplit=1)[1]
                elif line.startswith("IdentityFile"):
                    current["key_file"] = line.split(maxsplit=1)[1]
            if current:
                hosts.append(current)
        except OSError:
            pass
        return hosts


    # ── File transfer ──

    def upload_file(self, local_path: str, remote_path: str, name: str | None = None) -> tuple[bool, str]:
        """Upload a local file to the remote host via SCP."""
        host_name = name or self._active
        if not host_name:
            return False, "No active remote host"
        host = self._hosts.get(host_name)
        if not host:
            return False, f"Unknown host: {host_name}"

        scp_cmd = ["scp"]
        if host.port != 22:
            scp_cmd += ["-P", str(host.port)]
        if host.key_file:
            scp_cmd += ["-i", host.key_file]
        scp_cmd += [local_path, f"{host.ssh_target}:{remote_path}"]

        try:
            r = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=120)
            return r.returncode == 0, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            return False, "Upload timed out"
        except FileNotFoundError:
            return False, "scp not found"

    def download_file(self, remote_path: str, local_path: str, name: str | None = None) -> tuple[bool, str]:
        """Download a file from the remote host via SCP."""
        host_name = name or self._active
        if not host_name:
            return False, "No active remote host"
        host = self._hosts.get(host_name)
        if not host:
            return False, f"Unknown host: {host_name}"

        scp_cmd = ["scp"]
        if host.port != 22:
            scp_cmd += ["-P", str(host.port)]
        if host.key_file:
            scp_cmd += ["-i", host.key_file]
        scp_cmd += [f"{host.ssh_target}:{remote_path}", local_path]

        try:
            r = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=120)
            return r.returncode == 0, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            return False, "Download timed out"
        except FileNotFoundError:
            return False, "scp not found"

    def read_remote_file(self, remote_path: str, name: str | None = None) -> str:
        """Read a file on the remote host."""
        rc, out, err = self.run_remote(f"cat {remote_path}", name)
        return out if rc == 0 else f"Error: {err}"

    def write_remote_file(self, remote_path: str, content: str, name: str | None = None) -> tuple[bool, str]:
        """Write content to a file on the remote host."""
        import shlex
        escaped = shlex.quote(content)
        rc, out, err = self.run_remote(f"echo {escaped} > {remote_path}", name)
        return rc == 0, err if rc != 0 else "ok"

    # ── Remote system info ──

    def get_remote_info(self, name: str | None = None) -> dict[str, str]:
        """Get OS and system info from remote host."""
        rc, out, _ = self.run_remote("uname -a && whoami && pwd", name)
        if rc != 0:
            return {"error": "Failed to get info"}
        lines = out.strip().splitlines()
        return {
            "uname": lines[0] if len(lines) > 0 else "",
            "user": lines[1] if len(lines) > 1 else "",
            "cwd": lines[2] if len(lines) > 2 else "",
        }

    def list_remote_files(self, path: str = ".", name: str | None = None) -> list[str]:
        """List files on the remote host."""
        rc, out, _ = self.run_remote(f"ls -la {path}", name)
        return out.strip().splitlines() if rc == 0 else []

    # ── Reverse tunnel ──

    def create_reverse_tunnel(self, name: str, remote_port: int, local_port: int) -> subprocess.Popen | None:
        """Create a reverse SSH tunnel (remote -> local)."""
        host = self._hosts.get(name)
        if not host:
            return None
        args = ["ssh", "-N", "-R", f"{remote_port}:localhost:{local_port}"]
        if host.port != 22:
            args += ["-p", str(host.port)]
        if host.key_file:
            args += ["-i", host.key_file]
        args.append(host.ssh_target)
        try:
            return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception:
            return None

    # ── Import from SSH config ──

    def import_from_ssh_config(self) -> int:
        """Import hosts from ~/.ssh/config."""
        entries = self.parse_ssh_config()
        count = 0
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in self._hosts:
                continue
            self.add_host(
                name=name,
                host=entry.get("host", name),
                user=entry.get("user", ""),
                port=int(entry.get("port", "22")),
                key_file=entry.get("key_file", ""),
            )
            count += 1
        return count


# Module singleton
_manager: RemoteManager | None = None


def get_remote_manager() -> RemoteManager:
    global _manager
    if _manager is None:
        _manager = RemoteManager()
    return _manager
