from __future__ import annotations

import os
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from core.config import DATA_DIR
from manager.dev.runtime import run_shell
from manager.integrations.github_client import parse_repo_slug


def _workspace_root() -> Path:
    configured = str(os.getenv("DEV_WORKSPACE_ROOT", "") or "").strip()
    if configured:
        return Path(configured).resolve()
    return (Path(DATA_DIR) / "system" / "dev_workspaces").resolve()


def _slugify(value: str, fallback: str = "repo") -> str:
    raw = str(value or "").strip().lower()
    safe = []
    for ch in raw:
        if ch.isalnum() or ch in {"-", "_"}:
            safe.append(ch)
        else:
            safe.append("-")
    slug = "".join(safe).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or fallback


def _default_branch_name() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = uuid4().hex[:6]
    return f"manager-dev/{stamp}-{suffix}"


class DevWorkspaceManager:
    def __init__(self) -> None:
        self.root = _workspace_root()
        self.root.mkdir(parents=True, exist_ok=True)

    async def prepare_workspace(
        self,
        *,
        repo_path: str = "",
        repo_url: str = "",
        owner: str = "",
        repo: str = "",
    ) -> Dict[str, Any]:
        safe_repo_path = str(repo_path or "").strip()
        safe_repo_url = str(repo_url or "").strip()

        if safe_repo_path:
            path = Path(os.path.abspath(os.path.expanduser(safe_repo_path))).resolve()
        elif safe_repo_url:
            resolved_owner, resolved_repo = parse_repo_slug(safe_repo_url)
            slug = _slugify(f"{resolved_owner}-{resolved_repo}", fallback="repo")
            path = (self.root / slug).resolve()
            clone_result = await self._clone_or_pull(
                repo_url=safe_repo_url,
                target_path=str(path),
            )
            if not clone_result.get("ok"):
                return clone_result
        else:
            path = Path(os.getcwd()).resolve()

        if not path.exists() or not path.is_dir():
            return {
                "ok": False,
                "error_code": "workspace_not_found",
                "message": f"workspace does not exist: {path}",
            }

        git_check = await run_shell(
            "git rev-parse --is-inside-work-tree", cwd=str(path)
        )
        if not git_check.get("ok"):
            return {
                "ok": False,
                "error_code": "git_repo_required",
                "message": "workspace is not a git repository",
                "data": {"path": str(path)},
            }

        origin_url = await self._origin_url(str(path))
        parsed_owner, parsed_repo = parse_repo_slug(origin_url)
        safe_owner = str(owner or "").strip() or parsed_owner
        safe_repo = str(repo or "").strip() or parsed_repo
        default_branch = await self._default_branch(str(path))

        return {
            "ok": True,
            "path": str(path),
            "origin_url": origin_url,
            "owner": safe_owner,
            "repo": safe_repo,
            "default_branch": default_branch,
        }

    async def ensure_branch(
        self,
        *,
        repo_path: str,
        branch_name: str = "",
        base_branch: str = "",
    ) -> Dict[str, Any]:
        safe_repo_path = str(repo_path or "").strip()
        if not safe_repo_path:
            return {
                "ok": False,
                "error_code": "invalid_args",
                "message": "repo_path is required",
            }

        safe_branch = str(branch_name or "").strip() or _default_branch_name()
        safe_base = str(base_branch or "").strip()

        if safe_base:
            await run_shell(
                f"git fetch origin {shlex.quote(safe_base)}",
                cwd=safe_repo_path,
            )
            checkout_base = await run_shell(
                f"git checkout {shlex.quote(safe_base)}",
                cwd=safe_repo_path,
            )
            if not checkout_base.get("ok"):
                return {
                    "ok": False,
                    "error_code": "checkout_base_failed",
                    "message": str(checkout_base.get("summary") or "checkout failed"),
                    "data": checkout_base,
                }
            await run_shell(
                f"git pull --ff-only origin {shlex.quote(safe_base)}",
                cwd=safe_repo_path,
            )

        checkout_branch = await run_shell(
            f"git checkout -B {shlex.quote(safe_branch)}",
            cwd=safe_repo_path,
        )
        if not checkout_branch.get("ok"):
            return {
                "ok": False,
                "error_code": "checkout_branch_failed",
                "message": str(
                    checkout_branch.get("summary") or "branch checkout failed"
                ),
                "data": checkout_branch,
            }

        return {
            "ok": True,
            "branch_name": safe_branch,
            "base_branch": safe_base,
        }

    async def _clone_or_pull(
        self, *, repo_url: str, target_path: str
    ) -> Dict[str, Any]:
        safe_repo_url = str(repo_url or "").strip()
        safe_target_path = str(target_path or "").strip()
        if not safe_repo_url or not safe_target_path:
            return {
                "ok": False,
                "error_code": "invalid_args",
                "message": "repo_url and target_path are required",
            }

        target = Path(safe_target_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        if (target / ".git").exists():
            fetch_result = await run_shell("git fetch --all --prune", cwd=str(target))
            if not fetch_result.get("ok"):
                return {
                    "ok": False,
                    "error_code": "git_fetch_failed",
                    "message": str(fetch_result.get("summary") or "git fetch failed"),
                    "data": fetch_result,
                }
            return {"ok": True, "path": str(target), "operation": "fetch"}

        clone_result = await run_shell(
            f"git clone {shlex.quote(safe_repo_url)} {shlex.quote(str(target))}",
            cwd=str(target.parent),
        )
        if not clone_result.get("ok"):
            return {
                "ok": False,
                "error_code": "git_clone_failed",
                "message": str(clone_result.get("summary") or "git clone failed"),
                "data": clone_result,
            }
        return {"ok": True, "path": str(target), "operation": "clone"}

    async def _origin_url(self, repo_path: str) -> str:
        result = await run_shell("git config --get remote.origin.url", cwd=repo_path)
        if not result.get("ok"):
            return ""
        return str(result.get("stdout") or "").strip()

    async def _default_branch(self, repo_path: str) -> str:
        result = await run_shell(
            "git symbolic-ref refs/remotes/origin/HEAD", cwd=repo_path
        )
        if result.get("ok"):
            value = str(result.get("stdout") or "").strip()
            if value.startswith("refs/remotes/origin/"):
                candidate = value.removeprefix("refs/remotes/origin/").strip()
                if candidate:
                    return candidate
        for fallback in ("main", "master"):
            check = await run_shell(
                f"git show-ref --verify --quiet refs/heads/{shlex.quote(fallback)}",
                cwd=repo_path,
            )
            if check.get("ok"):
                return fallback
        return "main"


dev_workspace_manager = DevWorkspaceManager()
