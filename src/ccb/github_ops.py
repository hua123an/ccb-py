"""GitHub integration for ccb-py.

Uses ``gh`` CLI for authenticated operations — PR comments, issues,
code review, etc.  Falls back to the GitHub REST API when ``gh`` is
not available.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# gh CLI helpers
# ---------------------------------------------------------------------------

def _gh(*args: str, cwd: str | None = None, timeout: int = 30) -> tuple[int, str, str]:
    cmd = ["gh", *args]
    try:
        r = subprocess.run(
            cmd, cwd=cwd or os.getcwd(),
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", "gh CLI not found. Install: https://cli.github.com"
    except subprocess.TimeoutExpired:
        return 1, "", "gh command timed out"


def gh_available() -> bool:
    rc, _, _ = _gh("auth", "status")
    return rc == 0


def gh_user() -> str | None:
    rc, out, _ = _gh("auth", "status", "--active")
    if rc != 0:
        return None
    for line in out.splitlines():
        if "Logged in to" in line and "account" in line:
            parts = line.split("account")
            if len(parts) > 1:
                return parts[1].strip().split()[0]
    return None


# ---------------------------------------------------------------------------
# Repo info
# ---------------------------------------------------------------------------

def repo_info(cwd: str | None = None) -> dict[str, Any] | None:
    rc, out, _ = _gh("repo", "view", "--json",
                      "name,owner,url,defaultBranchRef,description,isPrivate",
                      cwd=cwd)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# PR operations
# ---------------------------------------------------------------------------

@dataclass
class PRComment:
    author: str
    body: str
    path: str = ""
    line: int = 0
    created_at: str = ""
    state: str = ""


def pr_list(cwd: str | None = None, state: str = "open", limit: int = 20) -> list[dict[str, Any]]:
    rc, out, _ = _gh("pr", "list", "--state", state, "--limit", str(limit),
                      "--json", "number,title,author,state,headRefName,url,createdAt",
                      cwd=cwd)
    if rc != 0:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def pr_view(number: int | None = None, cwd: str | None = None) -> dict[str, Any] | None:
    args = ["pr", "view", "--json",
            "number,title,body,author,state,headRefName,baseRefName,url,"
            "additions,deletions,changedFiles,reviewDecision,comments,reviews"]
    if number:
        args.insert(2, str(number))
    rc, out, _ = _gh(*args, cwd=cwd)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def pr_comments(number: int | None = None, cwd: str | None = None) -> list[PRComment]:
    """Get review comments for a PR."""
    pr = pr_view(number, cwd=cwd)
    if not pr:
        return []
    comments: list[PRComment] = []
    for c in pr.get("comments", []):
        comments.append(PRComment(
            author=c.get("author", {}).get("login", ""),
            body=c.get("body", ""),
            created_at=c.get("createdAt", ""),
        ))
    for r in pr.get("reviews", []):
        if r.get("body"):
            comments.append(PRComment(
                author=r.get("author", {}).get("login", ""),
                body=r.get("body", ""),
                state=r.get("state", ""),
                created_at=r.get("submittedAt", ""),
            ))
    return comments


def pr_diff(number: int | None = None, cwd: str | None = None) -> str:
    args = ["pr", "diff"]
    if number:
        args.insert(2, str(number))
    rc, out, _ = _gh(*args, cwd=cwd)
    return out if rc == 0 else ""


def pr_create(
    title: str,
    body: str = "",
    base: str = "",
    draft: bool = False,
    cwd: str | None = None,
) -> tuple[bool, str]:
    args = ["pr", "create", "--title", title]
    if body:
        args += ["--body", body]
    if base:
        args += ["--base", base]
    if draft:
        args.append("--draft")
    rc, out, err = _gh(*args, cwd=cwd, timeout=60)
    return rc == 0, (out + err).strip()


def pr_checkout(number: int, cwd: str | None = None) -> tuple[bool, str]:
    rc, out, err = _gh("pr", "checkout", str(number), cwd=cwd)
    return rc == 0, (out + err).strip()


# ---------------------------------------------------------------------------
# Issue operations
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    number: int = 0
    title: str = ""
    body: str = ""
    state: str = ""
    author: str = ""
    labels: list[str] = field(default_factory=list)
    url: str = ""
    created_at: str = ""


def issue_list(cwd: str | None = None, state: str = "open", limit: int = 20) -> list[Issue]:
    rc, out, _ = _gh("issue", "list", "--state", state, "--limit", str(limit),
                      "--json", "number,title,state,author,labels,url,createdAt",
                      cwd=cwd)
    if rc != 0:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return [
        Issue(
            number=i.get("number", 0),
            title=i.get("title", ""),
            state=i.get("state", ""),
            author=i.get("author", {}).get("login", ""),
            labels=[l.get("name", "") for l in i.get("labels", [])],
            url=i.get("url", ""),
            created_at=i.get("createdAt", ""),
        )
        for i in data
    ]


def issue_view(number: int, cwd: str | None = None) -> Issue | None:
    rc, out, _ = _gh("issue", "view", str(number),
                      "--json", "number,title,body,state,author,labels,url,createdAt",
                      cwd=cwd)
    if rc != 0:
        return None
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        return None
    return Issue(
        number=d.get("number", 0),
        title=d.get("title", ""),
        body=d.get("body", ""),
        state=d.get("state", ""),
        author=d.get("author", {}).get("login", ""),
        labels=[l.get("name", "") for l in d.get("labels", [])],
        url=d.get("url", ""),
        created_at=d.get("createdAt", ""),
    )


def issue_create(
    title: str,
    body: str = "",
    labels: list[str] | None = None,
    cwd: str | None = None,
) -> tuple[bool, str]:
    args = ["issue", "create", "--title", title]
    if body:
        args += ["--body", body]
    if labels:
        args += ["--label", ",".join(labels)]
    rc, out, err = _gh(*args, cwd=cwd, timeout=30)
    return rc == 0, (out + err).strip()


# ---------------------------------------------------------------------------
# Code review helpers
# ---------------------------------------------------------------------------

def review_pr(number: int | None = None, cwd: str | None = None) -> dict[str, Any]:
    """Gather all data needed for a thorough code review."""
    pr = pr_view(number, cwd=cwd)
    if not pr:
        return {"error": "Could not load PR"}
    diff = pr_diff(number, cwd=cwd)
    comments = pr_comments(number, cwd=cwd)
    return {
        "pr": pr,
        "diff": diff[:15000],
        "comments": [{"author": c.author, "body": c.body, "state": c.state} for c in comments],
    }


def generate_review_prompt(number: int | None = None, cwd: str | None = None) -> str:
    """Build a prompt for the LLM to review a PR."""
    data = review_pr(number, cwd=cwd)
    if "error" in data:
        return f"Error: {data['error']}"
    pr = data["pr"]
    return (
        f"Please review this Pull Request:\n\n"
        f"**{pr.get('title', '')}** (#{pr.get('number', '')})\n"
        f"Author: {pr.get('author', {}).get('login', '')}\n"
        f"Base: {pr.get('baseRefName', '')} ← {pr.get('headRefName', '')}\n"
        f"Changes: +{pr.get('additions', 0)} -{pr.get('deletions', 0)} "
        f"in {pr.get('changedFiles', 0)} files\n\n"
        f"Description:\n{pr.get('body', '(no description)')[:2000]}\n\n"
        f"Diff:\n```\n{data['diff']}\n```\n\n"
        "Please provide:\n"
        "1. Summary of changes\n"
        "2. Potential issues or bugs\n"
        "3. Code quality observations\n"
        "4. Suggestions for improvement\n"
        "5. Overall assessment (approve / request changes)"
    )


# ---------------------------------------------------------------------------
# Autofix
# ---------------------------------------------------------------------------

def generate_autofix_prompt(number: int | None = None, cwd: str | None = None) -> str:
    """Build a prompt for the LLM to auto-fix issues from PR comments."""
    comments = pr_comments(number, cwd=cwd)
    diff = pr_diff(number, cwd=cwd)
    comment_text = "\n".join(
        f"- [{c.author}] ({c.state}): {c.body[:500]}"
        for c in comments if c.body.strip()
    )
    return (
        "Based on the following PR review comments, generate fixes:\n\n"
        f"Review comments:\n{comment_text}\n\n"
        f"Current diff:\n```\n{diff[:10000]}\n```\n\n"
        "For each comment that requests a change, provide the fix."
    )


# ---------------------------------------------------------------------------
# PR merge / close / approve
# ---------------------------------------------------------------------------

def pr_merge(
    number: int | None = None,
    method: str = "merge",  # merge, squash, rebase
    cwd: str | None = None,
) -> tuple[bool, str]:
    """Merge a PR."""
    args = ["pr", "merge"]
    if number:
        args.append(str(number))
    args.append(f"--{method}")
    args.append("--auto")
    rc, out, err = _gh(*args, cwd=cwd, timeout=60)
    return rc == 0, (out + err).strip()


def pr_close(number: int | None = None, cwd: str | None = None) -> tuple[bool, str]:
    args = ["pr", "close"]
    if number:
        args.append(str(number))
    rc, out, err = _gh(*args, cwd=cwd)
    return rc == 0, (out + err).strip()


def pr_reopen(number: int, cwd: str | None = None) -> tuple[bool, str]:
    rc, out, err = _gh("pr", "reopen", str(number), cwd=cwd)
    return rc == 0, (out + err).strip()


def pr_review_submit(
    number: int | None = None,
    event: str = "APPROVE",  # APPROVE, REQUEST_CHANGES, COMMENT
    body: str = "",
    cwd: str | None = None,
) -> tuple[bool, str]:
    """Submit a PR review."""
    args = ["pr", "review"]
    if number:
        args.append(str(number))
    flag = {"APPROVE": "--approve", "REQUEST_CHANGES": "--request-changes",
            "COMMENT": "--comment"}.get(event, "--comment")
    args.append(flag)
    if body:
        args += ["--body", body]
    rc, out, err = _gh(*args, cwd=cwd)
    return rc == 0, (out + err).strip()


def pr_ready(number: int | None = None, cwd: str | None = None) -> tuple[bool, str]:
    """Mark PR as ready for review (remove draft)."""
    args = ["pr", "ready"]
    if number:
        args.append(str(number))
    rc, out, err = _gh(*args, cwd=cwd)
    return rc == 0, (out + err).strip()


# ---------------------------------------------------------------------------
# Issue close / comment / labels
# ---------------------------------------------------------------------------

def issue_close(number: int, cwd: str | None = None) -> tuple[bool, str]:
    rc, out, err = _gh("issue", "close", str(number), cwd=cwd)
    return rc == 0, (out + err).strip()


def issue_reopen(number: int, cwd: str | None = None) -> tuple[bool, str]:
    rc, out, err = _gh("issue", "reopen", str(number), cwd=cwd)
    return rc == 0, (out + err).strip()


def issue_comment(number: int, body: str, cwd: str | None = None) -> tuple[bool, str]:
    rc, out, err = _gh("issue", "comment", str(number), "--body", body, cwd=cwd)
    return rc == 0, (out + err).strip()


def issue_edit(
    number: int,
    title: str | None = None,
    body: str | None = None,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
    cwd: str | None = None,
) -> tuple[bool, str]:
    args = ["issue", "edit", str(number)]
    if title:
        args += ["--title", title]
    if body:
        args += ["--body", body]
    if add_labels:
        args += ["--add-label", ",".join(add_labels)]
    if remove_labels:
        args += ["--remove-label", ",".join(remove_labels)]
    rc, out, err = _gh(*args, cwd=cwd)
    return rc == 0, (out + err).strip()


# ---------------------------------------------------------------------------
# Release management
# ---------------------------------------------------------------------------

def release_list(cwd: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    rc, out, _ = _gh("release", "list", "--limit", str(limit),
                      "--json", "tagName,name,isDraft,isPrerelease,publishedAt",
                      cwd=cwd)
    if rc != 0:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def release_create(
    tag: str,
    title: str = "",
    notes: str = "",
    draft: bool = False,
    prerelease: bool = False,
    cwd: str | None = None,
) -> tuple[bool, str]:
    args = ["release", "create", tag]
    if title:
        args += ["--title", title]
    if notes:
        args += ["--notes", notes]
    if draft:
        args.append("--draft")
    if prerelease:
        args.append("--prerelease")
    rc, out, err = _gh(*args, cwd=cwd, timeout=60)
    return rc == 0, (out + err).strip()


# ---------------------------------------------------------------------------
# Workflow / Actions
# ---------------------------------------------------------------------------

def workflow_list(cwd: str | None = None) -> list[dict[str, Any]]:
    rc, out, _ = _gh("workflow", "list", "--json", "name,id,state", cwd=cwd)
    if rc != 0:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def run_list(cwd: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    rc, out, _ = _gh("run", "list", "--limit", str(limit),
                      "--json", "databaseId,displayTitle,status,conclusion,headBranch,createdAt",
                      cwd=cwd)
    if rc != 0:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def run_view(run_id: int, cwd: str | None = None) -> dict[str, Any] | None:
    rc, out, _ = _gh("run", "view", str(run_id), "--json",
                      "databaseId,displayTitle,status,conclusion,jobs",
                      cwd=cwd)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Gist
# ---------------------------------------------------------------------------

def gist_create(
    files: list[str],
    description: str = "",
    public: bool = False,
) -> tuple[bool, str]:
    args = ["gist", "create"]
    if description:
        args += ["--desc", description]
    if public:
        args.append("--public")
    args.extend(files)
    rc, out, err = _gh(*args, timeout=30)
    return rc == 0, (out + err).strip()
