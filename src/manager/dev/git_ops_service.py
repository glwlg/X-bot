from __future__ import annotations

import shlex
from typing import Any, Dict, List, Tuple

from manager.dev.publisher import (
    _changed_files_from_status,
    _parse_git_count,
    _push_permission_denied,
    _secret_file,
    _split_lines,
    ManagerDevPublisher,
)
from manager.dev.runtime import run_shell
from manager.dev.workspace_session_service import workspace_session_service
from manager.integrations.github_client import parse_repo_slug


def _short(text: str, limit: int = 240) -> str:
    payload = str(text or "").strip()
    if len(payload) <= limit:
        return payload
    return payload[:limit].rstrip() + "..."


def _parse_ahead_behind(text: str) -> Tuple[int, int]:
    parts = [item.strip() for item in str(text or "").split() if item.strip()]
    if len(parts) != 2:
        return 0, 0
    try:
        behind = max(0, int(parts[0]))
        ahead = max(0, int(parts[1]))
    except Exception:
        return 0, 0
    return ahead, behind


class GitOpsService:
    def __init__(self) -> None:
        self.publisher = ManagerDevPublisher()

    @staticmethod
    def _response(
        *,
        ok: bool,
        summary: str,
        text: str = "",
        data: Dict[str, Any] | None = None,
        error_code: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": bool(ok),
            "summary": str(summary or "").strip(),
            "text": str(text or summary or "").strip(),
            "data": dict(data or {}),
            "terminal": False,
        }
        if not ok:
            payload["error_code"] = str(error_code or "git_ops_failed").strip()
            payload["message"] = str(text or summary or "git ops failed").strip()
            payload["failure_mode"] = "fatal"
        return payload

    async def _resolve_repo_context(
        self,
        *,
        workspace_id: str = "",
        repo_root: str = "",
        base_branch: str = "",
    ) -> Dict[str, Any]:
        inspected = await workspace_session_service.inspect(
            workspace_id=workspace_id,
            repo_root=repo_root,
        )
        if not inspected.get("ok"):
            return {
                "ok": False,
                "error_code": str(inspected.get("error_code") or "workspace_not_found"),
                "message": str(
                    inspected.get("message")
                    or inspected.get("text")
                    or "workspace not found"
                ),
            }
        data = dict(inspected.get("data") or {})
        effective_base = (
            str(
                base_branch
                or data.get("base_branch")
                or data.get("default_branch")
                or "main"
            ).strip()
            or "main"
        )
        owner = str(data.get("owner") or "").strip()
        repo = str(data.get("repo") or "").strip()
        if (not owner or not repo) and str(data.get("origin_url") or "").strip():
            resolved_owner, resolved_repo = parse_repo_slug(
                str(data.get("origin_url") or "")
            )
            owner = owner or resolved_owner
            repo = repo or resolved_repo
        return {
            "ok": True,
            **data,
            "repo_root": str(data.get("repo_root") or repo_root or "").strip(),
            "base_branch": effective_base,
            "owner": owner,
            "repo": repo,
        }

    async def status(
        self,
        *,
        workspace_id: str = "",
        repo_root: str = "",
        base_branch: str = "",
    ) -> Dict[str, Any]:
        ctx = await self._resolve_repo_context(
            workspace_id=workspace_id,
            repo_root=repo_root,
            base_branch=base_branch,
        )
        if not ctx.get("ok"):
            return self._response(
                ok=False,
                summary="git_ops status failed",
                text=str(ctx.get("message") or "workspace not found"),
                error_code=str(ctx.get("error_code") or "workspace_not_found"),
            )

        safe_repo_root = str(ctx.get("repo_root") or "").strip()
        branch = await run_shell("git rev-parse --abbrev-ref HEAD", cwd=safe_repo_root)
        status = await run_shell("git status --short --branch", cwd=safe_repo_root)
        if not branch.get("ok") or not status.get("ok"):
            failure = branch if not branch.get("ok") else status
            return self._response(
                ok=False,
                summary="git_ops status failed",
                text=str(failure.get("summary") or "git status failed"),
                error_code=str(failure.get("error_code") or "git_status_failed"),
                data={"context": ctx, "failure": failure},
            )

        porcelain = await run_shell("git status --porcelain", cwd=safe_repo_root)
        ahead = await run_shell(
            f"git rev-list --left-right --count {shlex.quote(str(ctx.get('base_branch') or 'main'))}...HEAD",
            cwd=safe_repo_root,
        )
        dirty_files = _changed_files_from_status(str(porcelain.get("stdout") or ""))
        ahead_count, behind_count = _parse_ahead_behind(str(ahead.get("stdout") or ""))
        payload = {
            **ctx,
            "branch_name": str(branch.get("stdout") or "").strip(),
            "git_status": str(status.get("stdout") or "").strip(),
            "dirty_files": dirty_files,
            "is_dirty": bool(dirty_files),
            "ahead_count": ahead_count,
            "behind_count": behind_count,
        }
        summary = f"git status ready for `{payload['branch_name'] or 'unknown'}`"
        return self._response(ok=True, summary=summary, text=summary, data=payload)

    async def diff(
        self,
        *,
        workspace_id: str = "",
        repo_root: str = "",
        mode: str = "working",
        base_branch: str = "",
    ) -> Dict[str, Any]:
        ctx = await self._resolve_repo_context(
            workspace_id=workspace_id,
            repo_root=repo_root,
            base_branch=base_branch,
        )
        if not ctx.get("ok"):
            return self._response(
                ok=False,
                summary="git_ops diff failed",
                text=str(ctx.get("message") or "workspace not found"),
                error_code=str(ctx.get("error_code") or "workspace_not_found"),
            )

        safe_repo_root = str(ctx.get("repo_root") or "").strip()
        safe_mode = str(mode or "working").strip().lower() or "working"
        if safe_mode == "staged":
            diff_cmd = "git diff --cached"
            names_cmd = "git diff --cached --name-only"
        elif safe_mode == "base":
            diff_cmd = (
                f"git diff {shlex.quote(str(ctx.get('base_branch') or 'main'))}...HEAD"
            )
            names_cmd = f"git diff --name-only {shlex.quote(str(ctx.get('base_branch') or 'main'))}...HEAD"
        else:
            safe_mode = "working"
            diff_cmd = "git diff"
            names_cmd = "git diff --name-only"

        diff_result = await run_shell(diff_cmd, cwd=safe_repo_root)
        if not diff_result.get("ok"):
            return self._response(
                ok=False,
                summary="git_ops diff failed",
                text=str(diff_result.get("summary") or "git diff failed"),
                error_code=str(diff_result.get("error_code") or "git_diff_failed"),
                data={"context": ctx, "failure": diff_result},
            )
        names = await run_shell(names_cmd, cwd=safe_repo_root)
        changed_files = (
            _split_lines(str(names.get("stdout") or "")) if names.get("ok") else []
        )
        text = str(
            diff_result.get("stdout") or diff_result.get("summary") or ""
        ).strip()
        if not text:
            text = f"No {safe_mode} diff"
        return self._response(
            ok=True,
            summary=f"git {safe_mode} diff ready",
            text=text,
            data={**ctx, "mode": safe_mode, "changed_files": changed_files},
        )

    async def branches(
        self,
        *,
        workspace_id: str = "",
        repo_root: str = "",
    ) -> Dict[str, Any]:
        ctx = await self._resolve_repo_context(
            workspace_id=workspace_id, repo_root=repo_root
        )
        if not ctx.get("ok"):
            return self._response(
                ok=False,
                summary="git_ops branches failed",
                text=str(ctx.get("message") or "workspace not found"),
                error_code=str(ctx.get("error_code") or "workspace_not_found"),
            )
        safe_repo_root = str(ctx.get("repo_root") or "").strip()
        current = await run_shell("git rev-parse --abbrev-ref HEAD", cwd=safe_repo_root)
        branches = await run_shell(
            "git branch --all --verbose --no-abbrev", cwd=safe_repo_root
        )
        if not branches.get("ok"):
            return self._response(
                ok=False,
                summary="git_ops branches failed",
                text=str(branches.get("summary") or "git branch failed"),
                error_code=str(branches.get("error_code") or "git_branch_failed"),
                data={"context": ctx, "failure": branches},
            )
        return self._response(
            ok=True,
            summary="git branches ready",
            text=str(branches.get("stdout") or "").strip(),
            data={
                **ctx,
                "current_branch": str(current.get("stdout") or "").strip(),
            },
        )

    async def commit(
        self,
        *,
        workspace_id: str = "",
        repo_root: str = "",
        message: str = "",
    ) -> Dict[str, Any]:
        ctx = await self._resolve_repo_context(
            workspace_id=workspace_id, repo_root=repo_root
        )
        if not ctx.get("ok"):
            return self._response(
                ok=False,
                summary="git_ops commit failed",
                text=str(ctx.get("message") or "workspace not found"),
                error_code=str(ctx.get("error_code") or "workspace_not_found"),
            )
        safe_repo_root = str(ctx.get("repo_root") or "").strip()
        status = await run_shell("git status --porcelain", cwd=safe_repo_root)
        if not status.get("ok"):
            return self._response(
                ok=False,
                summary="git_ops commit failed",
                text=str(status.get("summary") or "git status failed"),
                error_code=str(status.get("error_code") or "git_status_failed"),
                data={"context": ctx, "failure": status},
            )
        changed_files = _changed_files_from_status(str(status.get("stdout") or ""))
        if not changed_files:
            return self._response(
                ok=False,
                summary="git_ops commit failed",
                text="no changes to commit",
                error_code="no_changes",
                data={**ctx, "changed_files": []},
            )
        sensitive = [path for path in changed_files if _secret_file(path)]
        if sensitive:
            return self._response(
                ok=False,
                summary="git_ops commit failed",
                text="refusing to commit sensitive file changes",
                error_code="sensitive_files_detected",
                data={**ctx, "files": sensitive},
            )
        add = await run_shell("git add -A", cwd=safe_repo_root)
        if not add.get("ok"):
            return self._response(
                ok=False,
                summary="git_ops commit failed",
                text=str(add.get("summary") or "git add failed"),
                error_code=str(add.get("error_code") or "git_add_failed"),
                data={"context": ctx, "failure": add},
            )
        staged = await run_shell("git diff --cached --name-only", cwd=safe_repo_root)
        staged_files = _split_lines(str(staged.get("stdout") or ""))
        if not staged_files:
            return self._response(
                ok=False,
                summary="git_ops commit failed",
                text="no staged changes after git add",
                error_code="no_staged_changes",
                data={**ctx, "changed_files": changed_files},
            )
        safe_message = str(message or "").strip() or "chore: update project"
        commit = await run_shell(
            f"git commit -m {shlex.quote(safe_message)}",
            cwd=safe_repo_root,
        )
        if not commit.get("ok"):
            return self._response(
                ok=False,
                summary="git_ops commit failed",
                text=str(commit.get("summary") or "git commit failed"),
                error_code=str(commit.get("error_code") or "git_commit_failed"),
                data={"context": ctx, "failure": commit},
            )
        sha = await run_shell("git rev-parse HEAD", cwd=safe_repo_root)
        branch = await run_shell("git rev-parse --abbrev-ref HEAD", cwd=safe_repo_root)
        return self._response(
            ok=True,
            summary="git commit completed",
            text=str(commit.get("summary") or "git commit completed").strip(),
            data={
                **ctx,
                "branch_name": str(
                    branch.get("stdout") or ctx.get("branch_name") or ""
                ).strip(),
                "commit_sha": str(sha.get("stdout") or "").strip(),
                "committed_files": staged_files,
                "commit_message": safe_message,
            },
        )

    async def push(
        self,
        *,
        workspace_id: str = "",
        repo_root: str = "",
        strategy: str = "auto",
        base_branch: str = "",
        branch_name: str = "",
        owner: str = "",
        repo: str = "",
    ) -> Dict[str, Any]:
        ctx = await self._resolve_repo_context(
            workspace_id=workspace_id,
            repo_root=repo_root,
            base_branch=base_branch,
        )
        if not ctx.get("ok"):
            return self._response(
                ok=False,
                summary="git_ops push failed",
                text=str(ctx.get("message") or "workspace not found"),
                error_code=str(ctx.get("error_code") or "workspace_not_found"),
            )
        safe_repo_root = str(ctx.get("repo_root") or "").strip()
        safe_strategy = str(strategy or "auto").strip().lower() or "auto"
        if safe_strategy not in {"auto", "origin", "fork"}:
            return self._response(
                ok=False,
                summary="git_ops push failed",
                text=f"unsupported push strategy: {safe_strategy}",
                error_code="invalid_args",
            )
        branch = await run_shell("git rev-parse --abbrev-ref HEAD", cwd=safe_repo_root)
        effective_branch = str(
            branch_name or branch.get("stdout") or ctx.get("branch_name") or ""
        ).strip()
        if not effective_branch or effective_branch == "HEAD":
            return self._response(
                ok=False,
                summary="git_ops push failed",
                text="unable to determine current branch",
                error_code="branch_name_missing",
                data={"context": ctx, "branch": branch},
            )
        status = await run_shell("git status --porcelain", cwd=safe_repo_root)
        dirty_files = _changed_files_from_status(str(status.get("stdout") or ""))
        if dirty_files:
            return self._response(
                ok=False,
                summary="git_ops push failed",
                text="working tree has uncommitted changes; commit or stash before push",
                error_code="dirty_worktree",
                data={
                    **ctx,
                    "changed_files": dirty_files,
                    "branch_name": effective_branch,
                },
            )
        safe_base = str(ctx.get("base_branch") or "main").strip() or "main"
        ahead = await run_shell(
            f"git rev-list --count {shlex.quote(safe_base)}..HEAD",
            cwd=safe_repo_root,
        )
        if not ahead.get("ok"):
            return self._response(
                ok=False,
                summary="git_ops push failed",
                text=str(
                    ahead.get("summary") or "failed to inspect commits ahead of base"
                ),
                error_code=str(ahead.get("error_code") or "git_ahead_check_failed"),
                data={
                    "context": ctx,
                    "failure": ahead,
                    "branch_name": effective_branch,
                },
            )
        if _parse_git_count(str(ahead.get("stdout") or "0")) <= 0:
            return self._response(
                ok=False,
                summary="git_ops push failed",
                text="no commits ahead of base branch to push",
                error_code="no_commits_ahead",
                data={**ctx, "branch_name": effective_branch},
            )

        safe_owner = str(owner or ctx.get("owner") or "").strip()
        safe_repo = str(repo or ctx.get("repo") or "").strip()
        origin_url = str(ctx.get("origin_url") or "").strip()
        remote_name = "origin"
        remote_url = origin_url
        head_ref = effective_branch
        fork_payload: Dict[str, Any] = {}

        if safe_strategy in {"auto", "origin"}:
            push_result = await run_shell(
                f"git push -u origin {shlex.quote(effective_branch)}",
                cwd=safe_repo_root,
            )
            if push_result.get("ok"):
                return self._response(
                    ok=True,
                    summary="git push completed",
                    text=str(
                        push_result.get("summary") or "git push completed"
                    ).strip(),
                    data={
                        **ctx,
                        "branch_name": effective_branch,
                        "remote_name": remote_name,
                        "remote_url": remote_url,
                        "head_ref": head_ref,
                        "push": push_result,
                        "fork": fork_payload,
                    },
                )
            if safe_strategy == "origin" or not _push_permission_denied(
                push_result.get("summary") or push_result.get("stderr") or ""
            ):
                return self._response(
                    ok=False,
                    summary="git_ops push failed",
                    text=str(push_result.get("summary") or "git push failed"),
                    error_code=str(push_result.get("error_code") or "git_push_failed"),
                    data={
                        "context": ctx,
                        "failure": push_result,
                        "branch_name": effective_branch,
                    },
                )

        if not safe_owner or not safe_repo:
            return self._response(
                ok=False,
                summary="git_ops push failed",
                text="owner/repo is required for fork push",
                error_code="repo_missing",
                data={**ctx, "branch_name": effective_branch},
            )
        try:
            fork_push = await self.publisher._push_via_fork(
                repo_path=safe_repo_root,
                upstream_owner=safe_owner,
                repo=safe_repo,
                branch_name=effective_branch,
            )
        except Exception as exc:
            return self._response(
                ok=False,
                summary="git_ops push failed",
                text=str(exc),
                error_code="fork_push_failed",
                data={**ctx, "branch_name": effective_branch},
            )
        if not fork_push.get("ok"):
            return self._response(
                ok=False,
                summary="git_ops push failed",
                text=str(fork_push.get("message") or "fork push failed"),
                error_code=str(fork_push.get("error_code") or "fork_push_failed"),
                data={
                    "context": ctx,
                    "failure": fork_push,
                    "branch_name": effective_branch,
                },
            )
        fork_payload = dict(fork_push.get("fork") or {})
        remote_name = str(fork_payload.get("remote_name") or "fork").strip() or "fork"
        remote_url = str(fork_payload.get("remote_url") or "").strip()
        head_ref = (
            str(fork_payload.get("head_ref") or f"{effective_branch}").strip()
            or effective_branch
        )
        push_result = dict(fork_push.get("push") or {})
        return self._response(
            ok=True,
            summary="git push completed",
            text=str(
                push_result.get("summary")
                or fork_push.get("summary")
                or "git push completed"
            ).strip(),
            data={
                **ctx,
                "branch_name": effective_branch,
                "remote_name": remote_name,
                "remote_url": remote_url,
                "head_ref": head_ref,
                "push": push_result,
                "fork": fork_payload,
            },
        )

    async def handle(
        self,
        *,
        action: str = "status",
        workspace_id: str = "",
        repo_root: str = "",
        mode: str = "working",
        base_branch: str = "",
        message: str = "",
        strategy: str = "auto",
        branch_name: str = "",
        owner: str = "",
        repo: str = "",
    ) -> Dict[str, Any]:
        safe_action = str(action or "status").strip().lower() or "status"
        if safe_action == "status":
            return await self.status(
                workspace_id=workspace_id,
                repo_root=repo_root,
                base_branch=base_branch,
            )
        if safe_action == "diff":
            return await self.diff(
                workspace_id=workspace_id,
                repo_root=repo_root,
                mode=mode,
                base_branch=base_branch,
            )
        if safe_action == "branches":
            return await self.branches(workspace_id=workspace_id, repo_root=repo_root)
        if safe_action == "commit":
            return await self.commit(
                workspace_id=workspace_id,
                repo_root=repo_root,
                message=message,
            )
        if safe_action == "push":
            return await self.push(
                workspace_id=workspace_id,
                repo_root=repo_root,
                strategy=strategy,
                base_branch=base_branch,
                branch_name=branch_name,
                owner=owner,
                repo=repo,
            )
        return self._response(
            ok=False,
            summary="git_ops failed",
            text=f"unsupported git_ops action: {safe_action}",
            error_code="unsupported_action",
        )


git_ops_service = GitOpsService()
