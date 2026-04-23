"""Tests for ccb.github_ops module."""
from unittest.mock import patch, MagicMock

import pytest

from ccb.github_ops import (
    _gh, gh_available, PRComment, Issue,
    pr_list, pr_comments, pr_create, pr_merge, pr_close, pr_reopen,
    pr_review_submit, pr_ready,
    issue_list, issue_view, issue_create, issue_close, issue_reopen,
    issue_comment, issue_edit,
    release_list, release_create,
    workflow_list, run_list,
    gist_create,
    review_pr, generate_review_prompt, generate_autofix_prompt,
    repo_info,
)


def _mock_gh(rc=0, stdout="", stderr=""):
    return patch("ccb.github_ops._gh", return_value=(rc, stdout, stderr))


class TestGhCLI:
    def test_gh_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            rc, out, err = _gh("auth", "status")
            assert rc == 127
            assert "not found" in err

    def test_gh_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 30)):
            rc, out, err = _gh("pr", "list")
            assert rc == 1
            assert "timed out" in err


class TestPROperations:
    def test_pr_list_success(self):
        import json
        data = [{"number": 1, "title": "Test PR"}]
        with _mock_gh(0, json.dumps(data)):
            result = pr_list()
            assert len(result) == 1
            assert result[0]["number"] == 1

    def test_pr_list_failure(self):
        with _mock_gh(1, "", "error"):
            assert pr_list() == []

    def test_pr_create(self):
        with _mock_gh(0, "https://github.com/owner/repo/pull/1"):
            ok, msg = pr_create("Test PR", body="desc", base="main", draft=True)
            assert ok

    def test_pr_merge_squash(self):
        with _mock_gh(0, "Merged"):
            ok, msg = pr_merge(1, method="squash")
            assert ok

    def test_pr_close(self):
        with _mock_gh(0, "Closed"):
            ok, msg = pr_close(1)
            assert ok

    def test_pr_reopen(self):
        with _mock_gh(0, "Reopened"):
            ok, msg = pr_reopen(1)
            assert ok

    def test_pr_review_submit(self):
        with _mock_gh(0, "Approved"):
            ok, msg = pr_review_submit(1, event="APPROVE", body="LGTM")
            assert ok

    def test_pr_review_request_changes(self):
        with _mock_gh(0, "Reviewed"):
            ok, msg = pr_review_submit(1, event="REQUEST_CHANGES", body="Fix bug")
            assert ok

    def test_pr_ready(self):
        with _mock_gh(0, "Ready"):
            ok, msg = pr_ready(1)
            assert ok


class TestPRComments:
    def test_pr_comments_with_reviews(self):
        import json
        pr_data = {
            "comments": [{"author": {"login": "user1"}, "body": "Nice!", "createdAt": "2024-01-01"}],
            "reviews": [{"author": {"login": "user2"}, "body": "Fix this", "state": "CHANGES_REQUESTED", "submittedAt": "2024-01-02"}],
        }
        with patch("ccb.github_ops.pr_view", return_value=pr_data):
            comments = pr_comments(1)
            assert len(comments) == 2
            assert comments[0].author == "user1"
            assert comments[1].state == "CHANGES_REQUESTED"

    def test_pr_comments_no_pr(self):
        with patch("ccb.github_ops.pr_view", return_value=None):
            assert pr_comments(1) == []


class TestIssueOperations:
    def test_issue_list(self):
        import json
        data = [{"number": 42, "title": "Bug", "state": "open", "author": {"login": "dev"},
                 "labels": [{"name": "bug"}], "url": "http://...", "createdAt": "2024-01-01"}]
        with _mock_gh(0, json.dumps(data)):
            issues = issue_list()
            assert len(issues) == 1
            assert issues[0].number == 42
            assert issues[0].labels == ["bug"]

    def test_issue_create(self):
        with _mock_gh(0, "https://github.com/owner/repo/issues/1"):
            ok, msg = issue_create("New bug", body="Steps...", labels=["bug", "urgent"])
            assert ok

    def test_issue_close(self):
        with _mock_gh(0, "Closed"):
            ok, msg = issue_close(42)
            assert ok

    def test_issue_reopen(self):
        with _mock_gh(0, "Reopened"):
            ok, msg = issue_reopen(42)
            assert ok

    def test_issue_comment(self):
        with _mock_gh(0, "Added"):
            ok, msg = issue_comment(42, "Fixed in PR #5")
            assert ok

    def test_issue_edit(self):
        with _mock_gh(0, "Updated"):
            ok, msg = issue_edit(42, title="New title", add_labels=["p1"], remove_labels=["p2"])
            assert ok


class TestReleases:
    def test_release_list(self):
        import json
        data = [{"tagName": "v1.0.0", "name": "Release 1"}]
        with _mock_gh(0, json.dumps(data)):
            releases = release_list()
            assert len(releases) == 1

    def test_release_create(self):
        with _mock_gh(0, "Created"):
            ok, msg = release_create("v1.1.0", title="New", notes="Changes", draft=True)
            assert ok


class TestWorkflows:
    def test_workflow_list(self):
        import json
        data = [{"name": "CI", "id": 1, "state": "active"}]
        with _mock_gh(0, json.dumps(data)):
            wfs = workflow_list()
            assert len(wfs) == 1

    def test_run_list(self):
        import json
        data = [{"databaseId": 1, "displayTitle": "Build", "status": "completed"}]
        with _mock_gh(0, json.dumps(data)):
            runs = run_list()
            assert len(runs) == 1


class TestGist:
    def test_gist_create(self):
        with _mock_gh(0, "https://gist.github.com/abc123"):
            ok, url = gist_create(["file.py"], description="My gist", public=True)
            assert ok


class TestReviewHelpers:
    def test_review_pr_error(self):
        with patch("ccb.github_ops.pr_view", return_value=None):
            result = review_pr(1)
            assert "error" in result

    def test_generate_review_prompt_error(self):
        with patch("ccb.github_ops.pr_view", return_value=None):
            result = generate_review_prompt(1)
            assert "Error" in result

    def test_repo_info_success(self):
        import json
        with _mock_gh(0, json.dumps({"name": "repo", "owner": {"login": "me"}})):
            info = repo_info()
            assert info is not None
            assert info["name"] == "repo"

    def test_repo_info_failure(self):
        with _mock_gh(1, "", "not a repo"):
            assert repo_info() is None
