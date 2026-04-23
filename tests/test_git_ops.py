"""Tests for ccb.git_ops module."""
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from ccb.git_ops import (
    git_available,
    git_root,
    current_branch,
    diff_stat,
    diff_text,
    diff_names,
    log,
    status,
    stage,
    commit,
    branches,
    checkout,
    stash_push,
    stash_pop,
    stash_list,
    undo_last_commit,
    is_clean,
    changed_files,
    untracked_files,
    repo_info,
    generate_commit_message_prompt,
)


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repository."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
    # Initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
    return str(repo)


class TestGitAvailability:
    def test_available_in_repo(self, git_repo):
        assert git_available(git_repo) is True

    def test_not_available_outside_repo(self, tmp_path):
        assert git_available(str(tmp_path)) is False

    def test_root(self, git_repo):
        root = git_root(git_repo)
        assert root is not None
        assert Path(root).exists()


class TestBranch:
    def test_current_branch(self, git_repo):
        br = current_branch(git_repo)
        assert br in ("main", "master")

    def test_list_branches(self, git_repo):
        br_list = branches(git_repo)
        assert len(br_list) >= 1
        assert any(b["current"] for b in br_list)

    def test_create_branch(self, git_repo):
        ok, _ = checkout("test-branch", create=True, cwd=git_repo)
        assert ok is True
        assert current_branch(git_repo) == "test-branch"


class TestDiff:
    def test_no_diff_clean(self, git_repo):
        stat = diff_stat(cwd=git_repo)
        assert stat.files_changed == 0

    def test_diff_after_change(self, git_repo):
        (Path(git_repo) / "README.md").write_text("# Changed\n")
        text = diff_text(cwd=git_repo)
        assert "Changed" in text
        names = diff_names(cwd=git_repo)
        assert "README.md" in names


class TestStagingAndCommit:
    def test_stage_and_commit(self, git_repo):
        (Path(git_repo) / "new.txt").write_text("hello")
        assert stage(cwd=git_repo) is True
        ok, msg = commit("test commit", cwd=git_repo)
        assert ok is True

    def test_status(self, git_repo):
        (Path(git_repo) / "x.txt").write_text("x")
        s = status(cwd=git_repo)
        assert "x.txt" in s


class TestUndoRedo:
    def test_undo_last_commit(self, git_repo):
        (Path(git_repo) / "y.txt").write_text("y")
        stage(cwd=git_repo)
        commit("to undo", cwd=git_repo)
        ok, _ = undo_last_commit(cwd=git_repo)
        assert ok is True

    def test_stash(self, git_repo):
        (Path(git_repo) / "z.txt").write_text("z")
        stage(cwd=git_repo)
        ok, _ = stash_push("test stash", cwd=git_repo)
        assert ok is True
        items = stash_list(cwd=git_repo)
        assert len(items) >= 1
        ok, _ = stash_pop(cwd=git_repo)
        assert ok is True


class TestLog:
    def test_log_entries(self, git_repo):
        entries = log(count=5, cwd=git_repo)
        assert len(entries) >= 1
        assert entries[0].subject == "init"


class TestWorktree:
    def test_is_clean(self, git_repo):
        assert is_clean(git_repo) is True
        (Path(git_repo) / "dirty.txt").write_text("d")
        assert is_clean(git_repo) is False

    def test_changed_files(self, git_repo):
        (Path(git_repo) / "ch.txt").write_text("c")
        files = changed_files(git_repo)
        assert "ch.txt" in files

    def test_untracked(self, git_repo):
        (Path(git_repo) / "un.txt").write_text("u")
        ut = untracked_files(git_repo)
        assert "un.txt" in ut


class TestRepoInfo:
    def test_repo_info(self, git_repo):
        info = repo_info(git_repo)
        assert info is not None
        assert info.branch in ("main", "master")
        assert info.commit_count >= 1
        d = info.to_dict()
        assert "root" in d

    def test_repo_info_not_git(self, tmp_path):
        assert repo_info(str(tmp_path)) is None


class TestCommitPrompt:
    def test_generate_prompt(self, git_repo):
        (Path(git_repo) / "README.md").write_text("# Updated\n")
        stage(cwd=git_repo)
        prompt = generate_commit_message_prompt(git_repo)
        assert "conventional commits" in prompt.lower()
        assert "Updated" in prompt
