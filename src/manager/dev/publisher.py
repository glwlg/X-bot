from __future__ import annotations

import shlex
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from manager.dev.deployment_targets import deployment_targets
from manager.dev.runtime import run_shell
from manager.integrations.gh_delivery_client import gh_delivery_client
from manager.integrations.github_client import GitHubClientError


def _split_lines(text: str) -> List[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _changed_files_from_status(text: str) -> List[str]:
    rows: List[str] = []
    for raw in _split_lines(text):
        if len(raw) >= 3 and raw[2] == " ":
            body = raw[3:]
        elif len(raw) >= 2:
            body = raw[2:]
        else:
            body = raw
        if "->" in body:
            body = body.split("->", 1)[1]
        token = body.strip()
        if token and token not in rows:
            rows.append(token)
    return rows


def _parse_git_count(text: str) -> int:
    try:
        return max(0, int(str(text or "").strip() or "0"))
    except Exception:
        return 0


def _push_permission_denied(text: str) -> bool:
    lowered = str(text or "").lower()
    return (
        "permission to" in lowered and "denied" in lowered
    ) or "requested url returned error: 403" in lowered


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
        self.github = gh_delivery_client

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

    @staticmethod
    def _fork_remote_url(*, fork_owner: str, repo: str) -> str:
        return f"https://github.com/{str(fork_owner or '').strip()}/{str(repo or '').strip()}.git"

    async def _ensure_git_remote(
        self,
        *,
        repo_path: str,
        remote_name: str,
        remote_url: str,
    ) -> Dict[str, Any]:
        safe_remote_name = str(remote_name or "").strip()
        safe_remote_url = str(remote_url or "").strip()
        existing = await run_shell(
            f"git remote get-url {shlex.quote(safe_remote_name)}",
            cwd=repo_path,
        )
        if bool(existing.get("ok")):
            current_url = str(existing.get("stdout") or "").strip()
            if current_url == safe_remote_url:
                return {
                    "ok": True,
                    "summary": f"remote {safe_remote_name} already configured",
                    "action": "unchanged",
                    "url": safe_remote_url,
                }
            updated = await run_shell(
                f"git remote set-url {shlex.quote(safe_remote_name)} {shlex.quote(safe_remote_url)}",
                cwd=repo_path,
            )
            if not updated.get("ok"):
                return {
                    "ok": False,
                    "error_code": "git_remote_set_url_failed",
                    "message": str(
                        updated.get("summary") or "git remote set-url failed"
                    ),
                    "data": updated,
                }
            return {
                "ok": True,
                "summary": f"remote {safe_remote_name} updated",
                "action": "updated",
                "url": safe_remote_url,
            }

        added = await run_shell(
            f"git remote add {shlex.quote(safe_remote_name)} {shlex.quote(safe_remote_url)}",
            cwd=repo_path,
        )
        if not added.get("ok"):
            return {
                "ok": False,
                "error_code": "git_remote_add_failed",
                "message": str(added.get("summary") or "git remote add failed"),
                "data": added,
            }
        return {
            "ok": True,
            "summary": f"remote {safe_remote_name} added",
            "action": "added",
            "url": safe_remote_url,
        }

    async def _push_via_fork(
        self,
        *,
        repo_path: str,
        upstream_owner: str,
        repo: str,
        branch_name: str,
    ) -> Dict[str, Any]:
        fork = await self.github.ensure_fork(owner=upstream_owner, repo=repo)
        fork_owner = str(fork.get("owner") or "").strip()
        fork_repo = str(fork.get("repo") or repo).strip()
        remote_url = self._fork_remote_url(fork_owner=fork_owner, repo=fork_repo)
        remote_result = await self._ensure_git_remote(
            repo_path=repo_path,
            remote_name="fork",
            remote_url=remote_url,
        )
        if not remote_result.get("ok"):
            return {
                "ok": False,
                "error_code": str(
                    remote_result.get("error_code") or "git_remote_config_failed"
                ),
                "message": str(
                    remote_result.get("message") or "failed to configure fork remote"
                ),
                "data": {"remote": remote_result, "fork": fork},
            }

        push = await run_shell(
            f"git push -u fork {shlex.quote(str(branch_name or '').strip())}",
            cwd=repo_path,
        )
        if not push.get("ok"):
            return {
                "ok": False,
                "error_code": "git_push_failed",
                "message": str(push.get("summary") or "git push to fork failed"),
                "data": {"push": push, "fork": fork, "remote": remote_result},
            }

        return {
            "ok": True,
            "summary": "pushed to fork",
            "push": push,
            "fork": {
                **dict(fork),
                "remote_name": "fork",
                "remote_url": remote_url,
                "head_ref": f"{fork_owner}:{str(branch_name or '').strip()}",
            },
            "remote": remote_result,
        }

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
                "message": "target_service must be manager or api",
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

        safe_base_branch = str(base_branch or "").strip() or "main"
        current_branch = await run_shell(
            "git rev-parse --abbrev-ref HEAD",
            cwd=safe_repo_path,
        )
        if not current_branch.get("ok"):
            return {
                "ok": False,
                "error_code": "git_branch_failed",
                "message": str(
                    current_branch.get("summary") or "failed to resolve current branch"
                ),
                "data": current_branch,
            }
        effective_branch_name = (
            str(current_branch.get("stdout") or "").strip()
            or str(branch_name or "").strip()
        )
        if effective_branch_name == "HEAD":
            effective_branch_name = str(branch_name or "").strip()
        if not effective_branch_name:
            return {
                "ok": False,
                "error_code": "branch_name_missing",
                "message": "unable to determine branch name for publish",
            }

        status = await run_shell("git status --porcelain", cwd=safe_repo_path)
        if not status.get("ok"):
            return {
                "ok": False,
                "error_code": "git_status_failed",
                "message": str(status.get("summary") or "git status failed"),
                "data": status,
                "branch_name": effective_branch_name,
                "base_branch": safe_base_branch,
            }

        safe_commit_message = (
            str(commit_message or "").strip() or "chore: update project"
        )
        changed_files = _changed_files_from_status(str(status.get("stdout") or ""))
        committed_files: List[str] = []
        commit: Dict[str, Any] = {"ok": True, "summary": "commit skipped"}
        dirty_worktree = bool(changed_files)

        if dirty_worktree:
            sensitive = [path for path in changed_files if _secret_file(path)]
            if sensitive:
                return {
                    "ok": False,
                    "error_code": "sensitive_files_detected",
                    "message": "refusing to publish sensitive file changes",
                    "data": {"files": sensitive},
                    "branch_name": effective_branch_name,
                    "base_branch": safe_base_branch,
                }

            add_result = await run_shell("git add -A", cwd=safe_repo_path)
            if not add_result.get("ok"):
                return {
                    "ok": False,
                    "error_code": "git_add_failed",
                    "message": str(add_result.get("summary") or "git add failed"),
                    "data": add_result,
                    "branch_name": effective_branch_name,
                    "base_branch": safe_base_branch,
                }

            staged = await run_shell(
                "git diff --cached --name-only", cwd=safe_repo_path
            )
            committed_files = _split_lines(str(staged.get("stdout") or ""))
            if not committed_files:
                return {
                    "ok": False,
                    "error_code": "no_staged_changes",
                    "message": "no staged changes after git add",
                    "branch_name": effective_branch_name,
                    "base_branch": safe_base_branch,
                }

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
                    "branch_name": effective_branch_name,
                    "base_branch": safe_base_branch,
                }
        else:
            ahead = await run_shell(
                f"git rev-list --count {shlex.quote(safe_base_branch)}..HEAD",
                cwd=safe_repo_path,
            )
            if not ahead.get("ok"):
                return {
                    "ok": False,
                    "error_code": "git_ahead_check_failed",
                    "message": str(
                        ahead.get("summary")
                        or "failed to inspect commits ahead of base"
                    ),
                    "data": ahead,
                    "branch_name": effective_branch_name,
                    "base_branch": safe_base_branch,
                }
            ahead_count = _parse_git_count(str(ahead.get("stdout") or "0"))
            if ahead_count <= 0:
                return {
                    "ok": False,
                    "error_code": "no_changes",
                    "message": "no changes to publish",
                    "branch_name": effective_branch_name,
                    "base_branch": safe_base_branch,
                }
            diff_files = await run_shell(
                f"git diff --name-only {shlex.quote(safe_base_branch)}..HEAD",
                cwd=safe_repo_path,
            )
            if not diff_files.get("ok"):
                return {
                    "ok": False,
                    "error_code": "git_diff_failed",
                    "message": str(diff_files.get("summary") or "git diff failed"),
                    "data": diff_files,
                    "branch_name": effective_branch_name,
                    "base_branch": safe_base_branch,
                }
            committed_files = _split_lines(str(diff_files.get("stdout") or ""))
            sensitive = [path for path in committed_files if _secret_file(path)]
            if sensitive:
                return {
                    "ok": False,
                    "error_code": "sensitive_files_detected",
                    "message": "refusing to publish sensitive committed changes",
                    "data": {"files": sensitive},
                    "branch_name": effective_branch_name,
                    "base_branch": safe_base_branch,
                }

        head_sha_result = await run_shell("git rev-parse HEAD", cwd=safe_repo_path)
        head_sha = str(head_sha_result.get("stdout") or "").strip()

        push_data: Dict[str, Any] = {"ok": True, "summary": "push skipped"}
        fork_payload: Dict[str, Any] = {}
        head_ref = effective_branch_name
        if bool(auto_push):
            push_data = await run_shell(
                f"git push -u origin {shlex.quote(effective_branch_name)}",
                cwd=safe_repo_path,
            )
            if not push_data.get("ok"):
                if _push_permission_denied(
                    push_data.get("summary") or push_data.get("stderr") or ""
                ):
                    try:
                        fork_push = await self._push_via_fork(
                            repo_path=safe_repo_path,
                            upstream_owner=str(owner or "").strip(),
                            repo=str(repo or "").strip(),
                            branch_name=effective_branch_name,
                        )
                    except GitHubClientError as exc:
                        return {
                            "ok": False,
                            "error_code": "fork_push_failed",
                            "message": str(exc),
                            "data": {"origin_push": push_data},
                            "branch_name": effective_branch_name,
                            "base_branch": safe_base_branch,
                        }
                    if not fork_push.get("ok"):
                        return {
                            "ok": False,
                            "error_code": str(
                                fork_push.get("error_code") or "fork_push_failed"
                            ),
                            "message": str(
                                fork_push.get("message") or "fork push failed"
                            ),
                            "data": {
                                "origin_push": push_data,
                                "fork_push": fork_push,
                            },
                            "branch_name": effective_branch_name,
                            "base_branch": safe_base_branch,
                        }
                    push_data = dict(fork_push.get("push") or {})
                    fork_payload = dict(fork_push.get("fork") or {})
                    head_ref = (
                        str(fork_payload.get("head_ref") or head_ref).strip()
                        or head_ref
                    )
                else:
                    return {
                        "ok": False,
                        "error_code": "git_push_failed",
                        "message": str(push_data.get("summary") or "git push failed"),
                        "data": push_data,
                        "branch_name": effective_branch_name,
                        "base_branch": safe_base_branch,
                    }

        pr_payload: Dict[str, Any] = {}
        if bool(auto_pr):
            if not auto_push:
                return {
                    "ok": False,
                    "error_code": "pr_requires_push",
                    "message": "auto_pr requires auto_push",
                    "branch_name": effective_branch_name,
                    "base_branch": safe_base_branch,
                }
            safe_owner = str(owner or "").strip()
            safe_repo = str(repo or "").strip()
            if not safe_owner or not safe_repo:
                return {
                    "ok": False,
                    "error_code": "repo_missing",
                    "message": "owner/repo is required for pull request creation",
                    "branch_name": effective_branch_name,
                    "base_branch": safe_base_branch,
                }
            safe_pr_title = str(pr_title or "").strip() or safe_commit_message
            safe_pr_body = str(pr_body or "").strip()
            try:
                pr_payload = await self.github.create_pull_request(
                    owner=safe_owner,
                    repo=safe_repo,
                    title=safe_pr_title,
                    head=head_ref,
                    base=safe_base_branch,
                    body=safe_pr_body,
                )
            except GitHubClientError as exc:
                return {
                    "ok": False,
                    "error_code": "create_pr_failed",
                    "message": str(exc),
                    "branch_name": effective_branch_name,
                    "base_branch": safe_base_branch,
                }

        return {
            "ok": True,
            "summary": "publish completed",
            "branch_name": effective_branch_name,
            "base_branch": safe_base_branch,
            "commit_sha": head_sha,
            "committed_files": committed_files,
            "dirty_worktree": dirty_worktree,
            "commit": commit,
            "push": push_data,
            "fork": fork_payload,
            "pull_request": pr_payload,
        }


manager_dev_publisher = ManagerDevPublisher()
