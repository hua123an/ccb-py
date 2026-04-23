"""Git integration for ccb-py.

Provides helpers for common git operations: diff, log, blame,
commit message generation, branch management, and undo/redo via
git stash/reflog.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Low-level git helpers
# ---------------------------------------------------------------------------

def _run(
    *args: str,
    cwd: str | Path | None = None,
    check: bool = False,
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Run a git command; return (returncode, stdout, stderr)."""
    cmd = ["git", *args]
    try:
        r = subprocess.run(
            cmd,
            cwd=cwd or os.getcwd(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", "git not found"
    except subprocess.TimeoutExpired:
        return 1, "", "git command timed out"


def git_available(cwd: str | None = None) -> bool:
    rc, _, _ = _run("rev-parse", "--is-inside-work-tree", cwd=cwd)
    return rc == 0


def git_root(cwd: str | None = None) -> str | None:
    rc, out, _ = _run("rev-parse", "--show-toplevel", cwd=cwd)
    return out.strip() if rc == 0 else None


def current_branch(cwd: str | None = None) -> str:
    rc, out, _ = _run("branch", "--show-current", cwd=cwd)
    return out.strip() if rc == 0 else "(detached)"


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

@dataclass
class DiffStat:
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    files: list[str] = field(default_factory=list)


def diff_stat(staged: bool = False, cwd: str | None = None) -> DiffStat:
    args = ["diff", "--stat"]
    if staged:
        args.append("--cached")
    rc, out, _ = _run(*args, cwd=cwd)
    if rc != 0:
        return DiffStat()
    stat = DiffStat()
    for line in out.strip().splitlines():
        if "|" in line:
            fname = line.split("|")[0].strip()
            stat.files.append(fname)
        elif "changed" in line:
            m = re.search(r"(\d+) file", line)
            if m:
                stat.files_changed = int(m.group(1))
            m = re.search(r"(\d+) insertion", line)
            if m:
                stat.insertions = int(m.group(1))
            m = re.search(r"(\d+) deletion", line)
            if m:
                stat.deletions = int(m.group(1))
    return stat


def diff_text(
    staged: bool = False,
    path: str | None = None,
    cwd: str | None = None,
    context_lines: int = 3,
) -> str:
    args = ["diff", f"-U{context_lines}"]
    if staged:
        args.append("--cached")
    if path:
        args += ["--", path]
    rc, out, _ = _run(*args, cwd=cwd)
    return out if rc == 0 else ""


def diff_names(staged: bool = False, cwd: str | None = None) -> list[str]:
    args = ["diff", "--name-only"]
    if staged:
        args.append("--cached")
    rc, out, _ = _run(*args, cwd=cwd)
    return out.strip().splitlines() if rc == 0 else []


# ---------------------------------------------------------------------------
# Log / Blame
# ---------------------------------------------------------------------------

@dataclass
class LogEntry:
    hash: str
    short_hash: str
    author: str
    date: str
    subject: str


def log(
    count: int = 20,
    path: str | None = None,
    cwd: str | None = None,
    oneline: bool = False,
) -> list[LogEntry]:
    fmt = "%H%x00%h%x00%an%x00%ai%x00%s"
    args = ["log", f"-n{count}", f"--pretty=format:{fmt}"]
    if path:
        args += ["--", path]
    rc, out, _ = _run(*args, cwd=cwd)
    if rc != 0:
        return []
    entries = []
    for line in out.strip().splitlines():
        parts = line.split("\0")
        if len(parts) >= 5:
            entries.append(LogEntry(*parts[:5]))
    return entries


def blame(path: str, cwd: str | None = None) -> str:
    rc, out, _ = _run("blame", "--date=short", path, cwd=cwd)
    return out if rc == 0 else ""


# ---------------------------------------------------------------------------
# Branch ops
# ---------------------------------------------------------------------------

def branches(cwd: str | None = None) -> list[dict[str, Any]]:
    rc, out, _ = _run("branch", "-vv", "--no-color", cwd=cwd)
    if rc != 0:
        return []
    result = []
    for line in out.strip().splitlines():
        is_current = line.startswith("*")
        name = line.lstrip("* ").split()[0]
        result.append({"name": name, "current": is_current, "raw": line.strip()})
    return result


def checkout(branch: str, create: bool = False, cwd: str | None = None) -> tuple[bool, str]:
    args = ["checkout"]
    if create:
        args.append("-b")
    args.append(branch)
    rc, out, err = _run(*args, cwd=cwd)
    return rc == 0, (out + err).strip()


def create_branch(name: str, cwd: str | None = None) -> tuple[bool, str]:
    return checkout(name, create=True, cwd=cwd)


# ---------------------------------------------------------------------------
# Staging & Commit
# ---------------------------------------------------------------------------

def status(short: bool = True, cwd: str | None = None) -> str:
    args = ["status"]
    if short:
        args.append("-s")
    rc, out, _ = _run(*args, cwd=cwd)
    return out.strip() if rc == 0 else ""


def stage(paths: list[str] | None = None, cwd: str | None = None) -> bool:
    args = ["add"]
    if paths:
        args += paths
    else:
        args.append("-A")
    rc, _, _ = _run(*args, cwd=cwd)
    return rc == 0


def commit(message: str, cwd: str | None = None) -> tuple[bool, str]:
    rc, out, err = _run("commit", "-m", message, cwd=cwd)
    return rc == 0, (out + err).strip()


def generate_commit_message_prompt(cwd: str | None = None) -> str:
    """Build a prompt for the LLM to generate a commit message from staged changes."""
    staged = diff_text(staged=True, cwd=cwd)
    if not staged:
        unstaged = diff_text(staged=False, cwd=cwd)
        if unstaged:
            staged = unstaged
    stat = diff_stat(staged=True, cwd=cwd)
    br = current_branch(cwd)
    return (
        "Generate a concise git commit message for the following changes.\n"
        "Use the conventional commits format: type(scope): description\n"
        "Types: feat, fix, docs, style, refactor, perf, test, chore, ci, build\n\n"
        f"Branch: {br}\n"
        f"Files changed: {stat.files_changed} (+{stat.insertions} -{stat.deletions})\n"
        f"Changed files: {', '.join(stat.files[:20])}\n\n"
        f"Diff:\n```\n{staged[:8000]}\n```\n\n"
        "Reply with ONLY the commit message, nothing else."
    )


# ---------------------------------------------------------------------------
# Undo / Redo via git
# ---------------------------------------------------------------------------

def stash_push(message: str = "", cwd: str | None = None) -> tuple[bool, str]:
    args = ["stash", "push", "-u"]
    if message:
        args += ["-m", message]
    rc, out, err = _run(*args, cwd=cwd)
    return rc == 0, (out + err).strip()


def stash_pop(cwd: str | None = None) -> tuple[bool, str]:
    rc, out, err = _run("stash", "pop", cwd=cwd)
    return rc == 0, (out + err).strip()


def stash_list(cwd: str | None = None) -> list[str]:
    rc, out, _ = _run("stash", "list", cwd=cwd)
    return out.strip().splitlines() if rc == 0 else []


def undo_last_commit(cwd: str | None = None) -> tuple[bool, str]:
    """Soft-reset the last commit, keeping changes staged."""
    rc, out, err = _run("reset", "--soft", "HEAD~1", cwd=cwd)
    return rc == 0, (out + err).strip()


def reflog(count: int = 10, cwd: str | None = None) -> list[str]:
    rc, out, _ = _run("reflog", f"-n{count}", "--oneline", cwd=cwd)
    return out.strip().splitlines() if rc == 0 else []


def restore_file(path: str, source: str = "HEAD", cwd: str | None = None) -> tuple[bool, str]:
    rc, out, err = _run("restore", "--source", source, "--", path, cwd=cwd)
    return rc == 0, (out + err).strip()


# ---------------------------------------------------------------------------
# Tag
# ---------------------------------------------------------------------------

def tags(cwd: str | None = None) -> list[str]:
    rc, out, _ = _run("tag", "--sort=-creatordate", cwd=cwd)
    return out.strip().splitlines() if rc == 0 else []


def create_tag(name: str, message: str = "", cwd: str | None = None) -> tuple[bool, str]:
    args = ["tag"]
    if message:
        args += ["-a", name, "-m", message]
    else:
        args.append(name)
    rc, out, err = _run(*args, cwd=cwd)
    return rc == 0, (out + err).strip()


# ---------------------------------------------------------------------------
# Remote
# ---------------------------------------------------------------------------

def remotes(cwd: str | None = None) -> list[dict[str, str]]:
    rc, out, _ = _run("remote", "-v", cwd=cwd)
    if rc != 0:
        return []
    result = []
    seen = set()
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] not in seen:
            seen.add(parts[0])
            result.append({"name": parts[0], "url": parts[1]})
    return result


def push(remote: str = "origin", branch: str | None = None, cwd: str | None = None) -> tuple[bool, str]:
    args = ["push", remote]
    if branch:
        args.append(branch)
    rc, out, err = _run(*args, cwd=cwd, timeout=60)
    return rc == 0, (out + err).strip()


def pull(remote: str = "origin", branch: str | None = None, cwd: str | None = None) -> tuple[bool, str]:
    args = ["pull", remote]
    if branch:
        args.append(branch)
    rc, out, err = _run(*args, cwd=cwd, timeout=60)
    return rc == 0, (out + err).strip()


def fetch(remote: str = "origin", cwd: str | None = None) -> tuple[bool, str]:
    rc, out, err = _run("fetch", remote, cwd=cwd, timeout=60)
    return rc == 0, (out + err).strip()


# ---------------------------------------------------------------------------
# Merge & Rebase
# ---------------------------------------------------------------------------

def merge(branch: str, no_ff: bool = False, cwd: str | None = None) -> tuple[bool, str]:
    args = ["merge"]
    if no_ff:
        args.append("--no-ff")
    args.append(branch)
    rc, out, err = _run(*args, cwd=cwd)
    return rc == 0, (out + err).strip()


def rebase(onto: str, interactive: bool = False, cwd: str | None = None) -> tuple[bool, str]:
    args = ["rebase"]
    if interactive:
        args.append("-i")
    args.append(onto)
    rc, out, err = _run(*args, cwd=cwd)
    return rc == 0, (out + err).strip()


def cherry_pick(commit_hash: str, cwd: str | None = None) -> tuple[bool, str]:
    rc, out, err = _run("cherry-pick", commit_hash, cwd=cwd)
    return rc == 0, (out + err).strip()


def abort_merge(cwd: str | None = None) -> tuple[bool, str]:
    rc, out, err = _run("merge", "--abort", cwd=cwd)
    return rc == 0, (out + err).strip()


def abort_rebase(cwd: str | None = None) -> tuple[bool, str]:
    rc, out, err = _run("rebase", "--abort", cwd=cwd)
    return rc == 0, (out + err).strip()


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def has_conflicts(cwd: str | None = None) -> bool:
    """Check if there are unresolved merge conflicts."""
    rc, out, _ = _run("diff", "--name-only", "--diff-filter=U", cwd=cwd)
    return rc == 0 and bool(out.strip())


def conflict_files(cwd: str | None = None) -> list[str]:
    rc, out, _ = _run("diff", "--name-only", "--diff-filter=U", cwd=cwd)
    return out.strip().splitlines() if rc == 0 else []


# ---------------------------------------------------------------------------
# Worktree info
# ---------------------------------------------------------------------------

def is_clean(cwd: str | None = None) -> bool:
    """Check if the working tree is clean (no uncommitted changes)."""
    rc, out, _ = _run("status", "--porcelain", cwd=cwd)
    return rc == 0 and not out.strip()


def changed_files(cwd: str | None = None) -> list[str]:
    """All files with any kind of change (staged + unstaged + untracked)."""
    rc, out, _ = _run("status", "--porcelain", cwd=cwd)
    if rc != 0:
        return []
    files = []
    for line in out.strip().splitlines():
        if len(line) > 3:
            files.append(line[3:].strip())
    return files


def untracked_files(cwd: str | None = None) -> list[str]:
    rc, out, _ = _run("ls-files", "--others", "--exclude-standard", cwd=cwd)
    return out.strip().splitlines() if rc == 0 else []


# ---------------------------------------------------------------------------
# Commit helpers
# ---------------------------------------------------------------------------

def amend_commit(message: str | None = None, cwd: str | None = None) -> tuple[bool, str]:
    args = ["commit", "--amend"]
    if message:
        args += ["-m", message]
    else:
        args.append("--no-edit")
    rc, out, err = _run(*args, cwd=cwd)
    return rc == 0, (out + err).strip()


def commit_with_message(message: str, all_files: bool = False, cwd: str | None = None) -> tuple[bool, str]:
    """Stage all (optionally) and commit."""
    if all_files:
        stage(cwd=cwd)
    return commit(message, cwd=cwd)


def show_commit(ref: str = "HEAD", cwd: str | None = None) -> str:
    rc, out, _ = _run("show", "--stat", ref, cwd=cwd)
    return out if rc == 0 else ""


# ---------------------------------------------------------------------------
# Repo info
# ---------------------------------------------------------------------------

@dataclass
class RepoInfo:
    root: str
    branch: str
    clean: bool
    remote_url: str
    commit_count: int
    last_commit: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "branch": self.branch,
            "clean": self.clean,
            "remote_url": self.remote_url,
            "commit_count": self.commit_count,
            "last_commit": self.last_commit,
        }


def repo_info(cwd: str | None = None) -> RepoInfo | None:
    root = git_root(cwd)
    if not root:
        return None
    br = current_branch(cwd)
    clean = is_clean(cwd)
    rems = remotes(cwd)
    remote_url = rems[0]["url"] if rems else ""
    rc, out, _ = _run("rev-list", "--count", "HEAD", cwd=cwd)
    count = int(out.strip()) if rc == 0 else 0
    entries = log(count=1, cwd=cwd)
    last = entries[0].subject if entries else ""
    return RepoInfo(root=root, branch=br, clean=clean, remote_url=remote_url,
                    commit_count=count, last_commit=last)
