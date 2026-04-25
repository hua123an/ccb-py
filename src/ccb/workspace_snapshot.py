"""Workspace snapshot and restore for ccb-py.

Captures complete project state beyond git stash:
- Git state (branch, commit, untracked files, stash)
- Environment variables (filtered)
- Dependency manifests hash
- Build artifacts state
- Session context

Snapshots are stored in ~/.claude/snapshots/<snapshot_id>.json
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WorkspaceSnapshot:
    """Complete workspace state capture."""
    id: str
    created_at: float
    description: str
    cwd: str
    git_root: str
    
    # Git state
    git_branch: str
    git_commit: str
    git_dirty: bool
    git_untracked_files: list[str] = field(default_factory=list)
    git_stash_list: list[dict] = field(default_factory=list)
    
    # Environment (filtered)
    env_vars: dict[str, str] = field(default_factory=dict)
    
    # Dependencies
    dep_manifests: dict[str, str] = field(default_factory=dict)  # path -> hash
    
    # Session context
    session_id: str = ""
    session_messages_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "description": self.description,
            "cwd": self.cwd,
            "git_root": self.git_root,
            "git_branch": self.git_branch,
            "git_commit": self.git_commit,
            "git_dirty": self.git_dirty,
            "git_untracked_files": self.git_untracked_files,
            "git_stash_list": self.git_stash_list,
            "env_vars": self.env_vars,
            "dep_manifests": self.dep_manifests,
            "session_id": self.session_id,
            "session_messages_count": self.session_messages_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkspaceSnapshot":
        return cls(**data)


class SnapshotManager:
    """Manage workspace snapshots."""
    
    DEP_MANIFESTS = [
        "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "requirements.txt", "pyproject.toml", "poetry.lock", "Pipfile", "Pipfile.lock",
        "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock",
        "go.mod", "go.sum", "composer.json", "composer.lock",
        "pom.xml", "build.gradle", "Cargo.toml",
    ]
    
    ENV_ALLOWLIST = [
        "PATH", "HOME", "USER", "SHELL", "TERM", "EDITOR",
        "LANG", "LC_ALL", "TZ", "PWD", "OLDPWD",
        "VIRTUAL_ENV", "CONDA_DEFAULT_ENV", "NODE_ENV",
        "PYTHONPATH", "JAVA_HOME", "GOPATH", "RUSTUP_HOME",
        "CC", "CXX", "AR", "LD",
    ]
    
    def __init__(self, storage_dir: Path | None = None):
        self._storage = storage_dir or Path.home() / ".claude" / "snapshots"
        self._storage.mkdir(parents=True, exist_ok=True)
    
    def _run_git(self, cwd: str, *args: str) -> tuple[int, str, str]:
        """Run git command, return (code, stdout, stderr)."""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return 1, "", "git not available or timeout"
    
    def _find_git_root(self, cwd: str) -> str | None:
        """Find git root for a directory."""
        code, out, _ = self._run_git(cwd, "rev-parse", "--show-toplevel")
        return out.strip() if code == 0 else None
    
    def _hash_file(self, path: Path) -> str:
        """Calculate SHA256 hash of file content."""
        try:
            content = path.read_bytes()
            return hashlib.sha256(content).hexdigest()[:16]
        except OSError:
            return ""
    
    def _capture_env(self) -> dict[str, str]:
        """Capture filtered environment variables."""
        return {k: v for k, v in os.environ.items() if k in self.ENV_ALLOWLIST}
    
    def _capture_deps(self, git_root: str) -> dict[str, str]:
        """Capture dependency manifest hashes."""
        root = Path(git_root)
        result = {}
        for manifest in self.DEP_MANIFESTS:
            path = root / manifest
            if path.exists():
                result[manifest] = self._hash_file(path)
        return result
    
    def create(
        self,
        cwd: str,
        description: str = "",
        session_id: str = "",
        session_messages_count: int = 0,
    ) -> WorkspaceSnapshot:
        """Create a new workspace snapshot."""
        snapshot_id = f"snap_{int(time.time())}_{os.urandom(4).hex()}"
        
        # Find git root
        git_root = self._find_git_root(cwd) or cwd
        
        # Git state
        code, branch, _ = self._run_git(git_root, "branch", "--show-current")
        git_branch = branch.strip() if code == 0 else "unknown"
        
        code, commit, _ = self._run_git(git_root, "rev-parse", "HEAD")
        git_commit = commit.strip()[:12] if code == 0 else "unknown"
        
        code, status_out, _ = self._run_git(git_root, "status", "--porcelain")
        git_dirty = bool(status_out.strip()) if code == 0 else False
        
        # Untracked files
        code, untracked, _ = self._run_git(
            git_root, "ls-files", "--others", "--exclude-standard"
        )
        git_untracked_files = [
            f for f in untracked.strip().split("\n") if f
        ] if code == 0 else []
        
        # Stash list
        code, stash_out, _ = self._run_git(git_root, "stash", "list")
        git_stash_list = [
            {"index": i, "message": line}
            for i, line in enumerate(stash_out.strip().split("\n"))
            if line
        ] if code == 0 else []
        
        snapshot = WorkspaceSnapshot(
            id=snapshot_id,
            created_at=time.time(),
            description=description or f"Snapshot at {git_branch}:{git_commit}",
            cwd=cwd,
            git_root=git_root,
            git_branch=git_branch,
            git_commit=git_commit,
            git_dirty=git_dirty,
            git_untracked_files=git_untracked_files,
            git_stash_list=git_stash_list,
            env_vars=self._capture_env(),
            dep_manifests=self._capture_deps(git_root),
            session_id=session_id,
            session_messages_count=session_messages_count,
        )
        
        # Save to disk
        self._save(snapshot)
        return snapshot
    
    def _save(self, snapshot: WorkspaceSnapshot) -> None:
        """Save snapshot to storage."""
        path = self._storage / f"{snapshot.id}.json"
        path.write_text(json.dumps(snapshot.to_dict(), indent=2))
    
    def load(self, snapshot_id: str) -> WorkspaceSnapshot | None:
        """Load a snapshot from storage."""
        path = self._storage / f"{snapshot_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return WorkspaceSnapshot.from_dict(data)
        except (json.JSONDecodeError, TypeError):
            return None
    
    def list_all(self) -> list[WorkspaceSnapshot]:
        """List all snapshots."""
        snapshots = []
        for path in sorted(self._storage.glob("snap_*.json"), reverse=True):
            try:
                data = json.loads(path.read_text())
                snapshots.append(WorkspaceSnapshot.from_dict(data))
            except (json.JSONDecodeError, TypeError):
                continue
        return snapshots
    
    def delete(self, snapshot_id: str) -> bool:
        """Delete a snapshot."""
        path = self._storage / f"{snapshot_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False
    
    def restore_git(self, snapshot: WorkspaceSnapshot, cwd: str) -> dict[str, Any]:
        """Restore git state from snapshot.
        
        Returns result dict with status and any errors.
        """
        results = {"success": True, "steps": []}
        git_root = self._find_git_root(cwd) or cwd
        
        # Check if we're in the right repo
        code, current_commit, _ = self._run_git(git_root, "rev-parse", "HEAD")
        if code != 0:
            return {"success": False, "error": "Not a git repository"}
        
        current_commit = current_commit.strip()[:12]
        
        # Stash current changes if dirty
        code, _, _ = self._run_git(git_root, "diff", "--quiet")
        if code != 0:
            self._run_git(git_root, "stash", "push", "-m", f"pre-snapshot-restore-{snapshot.id[:8]}")
            results["steps"].append("stashed current changes")
        
        # Checkout target commit
        if current_commit != snapshot.git_commit:
            code, out, err = self._run_git(git_root, "checkout", snapshot.git_commit)
            if code != 0:
                results["success"] = False
                results["error"] = f"Failed to checkout {snapshot.git_commit}: {err}"
                return results
            results["steps"].append(f"checked out {snapshot.git_commit}")
        
        # Note: We don't restore untracked files automatically for safety
        if snapshot.git_untracked_files:
            results["steps"].append(f"note: {len(snapshot.git_untracked_files)} untracked files in snapshot (not auto-restored)")
        
        return results
    
    def compare(self, snapshot: WorkspaceSnapshot, cwd: str) -> dict[str, Any]:
        """Compare current workspace with snapshot.
        
        Returns dict showing what's changed.
        """
        git_root = self._find_git_root(cwd) or cwd
        changes = {
            "git_branch_changed": False,
            "git_commit_changed": False,
            "deps_changed": [],
            "untracked_files_count": 0,
        }
        
        # Git comparison
        code, current_branch, _ = self._run_git(git_root, "branch", "--show-current")
        if code == 0:
            current_branch = current_branch.strip()
            if current_branch != snapshot.git_branch:
                changes["git_branch_changed"] = True
                changes["current_branch"] = current_branch
                changes["snapshot_branch"] = snapshot.git_branch
        
        code, current_commit, _ = self._run_git(git_root, "rev-parse", "HEAD")
        if code == 0:
            current_commit = current_commit.strip()[:12]
            if current_commit != snapshot.git_commit:
                changes["git_commit_changed"] = True
                changes["current_commit"] = current_commit
                changes["snapshot_commit"] = snapshot.git_commit
        
        # Dependencies comparison
        current_deps = self._capture_deps(git_root)
        for manifest, snap_hash in snapshot.dep_manifests.items():
            curr_hash = current_deps.get(manifest, "")
            if curr_hash != snap_hash:
                changes["deps_changed"].append(manifest)
        
        # Untracked files
        code, untracked, _ = self._run_git(
            git_root, "ls-files", "--others", "--exclude-standard"
        )
        changes["untracked_files_count"] = len([
            f for f in untracked.strip().split("\n") if f
        ]) if code == 0 else 0
        
        return changes


# Module singleton
_manager: SnapshotManager | None = None


def get_snapshot_manager() -> SnapshotManager:
    global _manager
    if _manager is None:
        _manager = SnapshotManager()
    return _manager
