from __future__ import annotations

import shlex
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from manager.dev.deployment_targets import deployment_targets
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

    @staticmethod
    def _rollout_target(target_service: str) -> Dict[str, str] | None:
        return deployment_targets.get(target_service)

    @staticmethod
    def _compose_root(repo_path: str) -> str:
        current = Path(str(repo_path or "").strip() or ".").resolve()
        for candidate in [current, *current.parents]:
            if (candidate / "docker-compose.yml").exists():
                return str(candidate)
        return str(current)

    async def _rollback_local(
        self,
        *,
        compose_root: str,
        service_name: str,
        image_name: str,
        backup_tag: str,
    ) -> Dict[str, Any]:
        safe_backup_tag = str(backup_tag or "").strip()
        if not safe_backup_tag:
            return {
                "attempted": False,
                "ok": False,
                "message": "no rollback snapshot available",
            }

        retag = await run_shell(
            f"docker tag {shlex.quote(safe_backup_tag)} {shlex.quote(image_name)}",
            cwd=compose_root,
            timeout_sec=180,
        )
        restore = await run_shell(
            f"docker compose up -d --no-build {shlex.quote(service_name)}",
            cwd=compose_root,
            timeout_sec=600,
        )
        return {
            "attempted": True,
            "ok": bool(retag.get("ok")) and bool(restore.get("ok")),
            "retag": retag,
            "restore": restore,
        }

    async def rollout_local(
        self,
        *,
        repo_path: str,
        target_service: str,
    ) -> Dict[str, Any]:
        target = self._rollout_target(target_service)
        if not target:
            return {
                "ok": False,
                "error_code": "invalid_target_service",
                "message": "target_service must be manager, worker, or api",
            }

        compose_root = self._compose_root(repo_path)
        service_name = str(target.get("service") or "").strip()
        image_name = str(target.get("image") or "").strip()
        snapshot: Dict[str, Any] = {
            "target_service": str(target_service or "").strip().lower(),
            "compose_root": compose_root,
            "service_name": service_name,
            "image_name": image_name,
        }

        backup_tag = ""
        inspect = await run_shell(
            f"docker image inspect {shlex.quote(image_name)} --format '{{{{.Id}}}}'",
            cwd=compose_root,
            timeout_sec=120,
        )
        if bool(inspect.get("ok")) and str(inspect.get("stdout") or "").strip():
            snapshot["previous_image_id"] = str(inspect.get("stdout") or "").strip()
            backup_tag = (
                f"{image_name}:rollback-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
            snapshot["backup_tag"] = backup_tag
            snapshot["backup"] = await run_shell(
                f"docker tag {shlex.quote(image_name)} {shlex.quote(backup_tag)}",
                cwd=compose_root,
                timeout_sec=120,
            )

        build = await run_shell(
            f"docker compose build {shlex.quote(service_name)}",
            cwd=compose_root,
            timeout_sec=1800,
        )
        if not build.get("ok"):
            return {
                "ok": False,
                "error_code": "rollout_build_failed",
                "message": str(build.get("summary") or "rollout build failed"),
                "snapshot": snapshot,
                "build": build,
                "rollback": {
                    "attempted": False,
                    "ok": False,
                    "message": "build failed before service update",
                },
            }

        up = await run_shell(
            f"docker compose up -d {shlex.quote(service_name)}",
            cwd=compose_root,
            timeout_sec=900,
        )
        if not up.get("ok"):
            rollback = await self._rollback_local(
                compose_root=compose_root,
                service_name=service_name,
                image_name=image_name,
                backup_tag=backup_tag,
            )
            return {
                "ok": False,
                "error_code": "rollout_up_failed",
                "message": str(up.get("summary") or "rollout up failed"),
                "snapshot": snapshot,
                "build": build,
                "up": up,
                "rollback": rollback,
            }

        ps = await run_shell(
            f"docker compose ps {shlex.quote(service_name)}",
            cwd=compose_root,
            timeout_sec=120,
        )
        return {
            "ok": True,
            "summary": f"local rollout completed for {service_name}",
            "target_service": str(target_service or "").strip().lower(),
            "snapshot": snapshot,
            "build": build,
            "up": up,
            "ps": ps,
        }

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
