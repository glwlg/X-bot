from __future__ import annotations

import shlex
from typing import Any, Dict, List

from manager.dev.runtime import run_shell
from manager.integrations.github_client import GitHubClientError, github_client


def _split_lines(text: str) -> List[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _changed_files_from_status(text: str) -> List[str]:
    rows: List[str] = []
    for raw in _split_lines(text):
        body = raw[3:] if len(raw) >= 3 else raw
        if "->" in body:
            body = body.split("->", 1)[1]
        token = body.strip()
        if token and token not in rows:
            rows.append(token)
    return rows


def _secret_file(path: str) -> bool:
    normalized = str(path or "").strip().lower()
    if not normalized:
        return False
    if normalized == ".env" or normalized.startswith(".env."):
        return True
    sensitive = (
        "credentials",
        "secret",
        "private_key",
        "id_rsa",
        "token",
        "apikey",
        "api_key",
    )
    return any(item in normalized for item in sensitive)


class ManagerDevPublisher:
    def __init__(self) -> None:
        self.github = github_client

    async def publish(
        self,
        *,
        repo_path: str,
        owner: str,
        repo: str,
        branch_name: str,
        base_branch: str,
        commit_message: str,
        pr_title: str,
        pr_body: str,
        auto_push: bool,
        auto_pr: bool,
    ) -> Dict[str, Any]:
        safe_repo_path = str(repo_path or "").strip()
        if not safe_repo_path:
            return {
                "ok": False,
                "error_code": "invalid_args",
                "message": "repo_path is required",
            }

        status = await run_shell("git status --porcelain", cwd=safe_repo_path)
        if not status.get("ok"):
            return {
                "ok": False,
                "error_code": "git_status_failed",
                "message": str(status.get("summary") or "git status failed"),
                "data": status,
            }

        changed_files = _changed_files_from_status(str(status.get("stdout") or ""))
        if not changed_files:
            return {
                "ok": False,
                "error_code": "no_changes",
                "message": "no changes to publish",
            }

        sensitive = [path for path in changed_files if _secret_file(path)]
        if sensitive:
            return {
                "ok": False,
                "error_code": "sensitive_files_detected",
                "message": "refusing to publish sensitive file changes",
                "data": {"files": sensitive},
            }

        add_result = await run_shell("git add -A", cwd=safe_repo_path)
        if not add_result.get("ok"):
            return {
                "ok": False,
                "error_code": "git_add_failed",
                "message": str(add_result.get("summary") or "git add failed"),
                "data": add_result,
            }

        staged = await run_shell("git diff --cached --name-only", cwd=safe_repo_path)
        staged_files = _split_lines(str(staged.get("stdout") or ""))
        if not staged_files:
            return {
                "ok": False,
                "error_code": "no_staged_changes",
                "message": "no staged changes after git add",
            }

        safe_commit_message = (
            str(commit_message or "").strip() or "chore: update project"
        )
        commit = await run_shell(
            f"git commit -m {shlex.quote(safe_commit_message)}",
            cwd=safe_repo_path,
        )
        if not commit.get("ok"):
            return {
                "ok": False,
                "error_code": "git_commit_failed",
                "message": str(commit.get("summary") or "git commit failed"),
                "data": commit,
            }

        head_sha_result = await run_shell("git rev-parse HEAD", cwd=safe_repo_path)
        head_sha = str(head_sha_result.get("stdout") or "").strip()

        push_data: Dict[str, Any] = {"ok": True, "summary": "push skipped"}
        if bool(auto_push):
            push_data = await run_shell(
                f"git push -u origin {shlex.quote(str(branch_name or '').strip())}",
                cwd=safe_repo_path,
            )
            if not push_data.get("ok"):
                return {
                    "ok": False,
                    "error_code": "git_push_failed",
                    "message": str(push_data.get("summary") or "git push failed"),
                    "data": push_data,
                }

        pr_payload: Dict[str, Any] = {}
        if bool(auto_pr):
            if not auto_push:
                return {
                    "ok": False,
                    "error_code": "pr_requires_push",
                    "message": "auto_pr requires auto_push",
                }
            safe_owner = str(owner or "").strip()
            safe_repo = str(repo or "").strip()
            if not safe_owner or not safe_repo:
                return {
                    "ok": False,
                    "error_code": "repo_missing",
                    "message": "owner/repo is required for pull request creation",
                }
            safe_pr_title = str(pr_title or "").strip() or safe_commit_message
            safe_pr_body = str(pr_body or "").strip()
            try:
                pr_payload = await self.github.create_pull_request(
                    owner=safe_owner,
                    repo=safe_repo,
                    title=safe_pr_title,
                    head=str(branch_name or "").strip(),
                    base=str(base_branch or "").strip() or "main",
                    body=safe_pr_body,
                )
            except GitHubClientError as exc:
                return {
                    "ok": False,
                    "error_code": "create_pr_failed",
                    "message": str(exc),
                }

        return {
            "ok": True,
            "summary": "publish completed",
            "commit_sha": head_sha,
            "staged_files": staged_files,
            "push": push_data,
            "pull_request": pr_payload,
        }


manager_dev_publisher = ManagerDevPublisher()
