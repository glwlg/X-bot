from __future__ import annotations

import json
import os
import shlex
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from manager.dev.runtime import run_shell
from manager.dev.session_paths import (
    new_workspace_id,
    repo_mirror_root,
    workspace_root,
    workspace_state_path,
)
from manager.integrations.github_client import parse_repo_slug


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _slugify(value: str, fallback: str = "repo") -> str:
    raw = str(value or "").strip().lower()
    rows: List[str] = []
    for ch in raw:
        rows.append(ch if ch.isalnum() or ch in {"-", "_"} else "-")
    slug = "".join(rows).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or fallback


def _default_branch_name() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"manager-dev/{stamp}-{new_workspace_id().split('-')[-1]}"


def _short(text: str, limit: int = 240) -> str:
    payload = str(text or "").strip()
    if len(payload) <= limit:
        return payload
    return payload[:limit].rstrip() + "..."


def _parse_status_files(text: str) -> List[str]:
    rows: List[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith("## "):
            continue
        if len(line) >= 3 and line[2] == " ":
            candidate = line[3:].strip()
        elif len(line) >= 2:
            candidate = line[2:].strip()
        else:
            candidate = ""
        if candidate.startswith('"') and candidate.endswith('"'):
            candidate = candidate[1:-1]
        if candidate and candidate not in rows:
            rows.append(candidate)
    return rows


class WorkspaceSessionService:
    def __init__(self) -> None:
        pass

    @staticmethod
    def _state_root() -> Path:
        return workspace_state_path("placeholder").parent

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
            payload["error_code"] = str(error_code or "workspace_failed").strip()
            payload["message"] = str(text or summary or "workspace failed").strip()
            payload["failure_mode"] = "fatal"
        return payload

    async def _load_state(self, workspace_id: str) -> Dict[str, Any] | None:
        path = workspace_state_path(workspace_id)
        if not path.exists():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return dict(loaded) if isinstance(loaded, dict) else None

    async def _save_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(payload or {})
        workspace_id = str(record.get("workspace_id") or "").strip()
        if not workspace_id:
            raise ValueError("workspace_id is required")
        record["updated_at"] = _now_iso()
        path = workspace_state_path(workspace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return record

    async def _list_states(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        state_root = self._state_root()
        if not state_root.exists():
            return rows
        for path in sorted(
            state_root.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(loaded, dict):
                rows.append(loaded)
        return rows

    async def _verify_git_repo(self, repo_path: str) -> Dict[str, Any]:
        return await run_shell("git rev-parse --is-inside-work-tree", cwd=repo_path)

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
            local = await run_shell(
                f"git show-ref --verify --quiet refs/heads/{shlex.quote(fallback)}",
                cwd=repo_path,
            )
            if local.get("ok"):
                return fallback
            remote = await run_shell(
                f"git show-ref --verify --quiet refs/remotes/origin/{shlex.quote(fallback)}",
                cwd=repo_path,
            )
            if remote.get("ok"):
                return fallback
        return "main"

    async def _resolve_start_ref(self, repo_path: str, base_branch: str) -> str:
        safe_base = str(base_branch or "").strip()
        if not safe_base:
            return "HEAD"
        remote = await run_shell(
            f"git show-ref --verify --quiet refs/remotes/origin/{shlex.quote(safe_base)}",
            cwd=repo_path,
        )
        if remote.get("ok"):
            return f"origin/{safe_base}"
        local = await run_shell(
            f"git show-ref --verify --quiet refs/heads/{shlex.quote(safe_base)}",
            cwd=repo_path,
        )
        if local.get("ok"):
            return safe_base
        return "HEAD"

    async def _prepare_mirror(self, repo_url: str) -> Dict[str, Any]:
        owner, repo = parse_repo_slug(repo_url)
        repo_slug = _slugify(f"{owner}-{repo}", fallback="repo")
        root = repo_mirror_root(repo_slug)
        if (root / ".git").exists():
            fetch = await run_shell("git fetch --all --prune", cwd=str(root))
            if not fetch.get("ok"):
                return {
                    "ok": False,
                    "error_code": "git_fetch_failed",
                    "message": str(fetch.get("summary") or "git fetch failed"),
                    "data": fetch,
                }
        else:
            root.parent.mkdir(parents=True, exist_ok=True)
            clone = await run_shell(
                f"git clone {shlex.quote(str(repo_url).strip())} {shlex.quote(str(root))}",
                cwd=str(root.parent),
            )
            if not clone.get("ok"):
                return {
                    "ok": False,
                    "error_code": "git_clone_failed",
                    "message": str(clone.get("summary") or "git clone failed"),
                    "data": clone,
                }
        default_branch = await self._default_branch(str(root))
        return {
            "ok": True,
            "source_root": str(root),
            "repo_slug": repo_slug,
            "origin_url": str(repo_url).strip(),
            "owner": owner,
            "repo": repo,
            "default_branch": default_branch,
            "source_type": "repo_url",
        }

    async def _prepare_local_source(self, repo_path: str) -> Dict[str, Any]:
        path = Path(os.path.abspath(os.path.expanduser(str(repo_path or "")))).resolve()
        if not path.exists() or not path.is_dir():
            return {
                "ok": False,
                "error_code": "workspace_not_found",
                "message": f"workspace does not exist: {path}",
            }
        top = await run_shell("git rev-parse --show-toplevel", cwd=str(path))
        if not top.get("ok"):
            return {
                "ok": False,
                "error_code": "git_repo_required",
                "message": "workspace is not a git repository",
                "data": {"path": str(path)},
            }
        source_root = str(top.get("stdout") or "").strip() or str(path)
        origin_url = await self._origin_url(source_root)
        owner, repo = parse_repo_slug(origin_url)
        repo_slug = _slugify(
            f"{owner}-{repo}", fallback=Path(source_root).name or "repo"
        )
        default_branch = await self._default_branch(source_root)
        return {
            "ok": True,
            "source_root": source_root,
            "repo_slug": repo_slug,
            "origin_url": origin_url,
            "owner": owner,
            "repo": repo,
            "default_branch": default_branch,
            "source_type": "repo_path",
        }

    async def prepare(
        self,
        *,
        repo_url: str = "",
        repo_path: str = "",
        base_branch: str = "",
        branch_name: str = "",
        mode: str = "fresh_worktree",
    ) -> Dict[str, Any]:
        safe_repo_url = str(repo_url or "").strip()
        safe_repo_path = str(repo_path or "").strip()
        safe_mode = str(mode or "fresh_worktree").strip().lower() or "fresh_worktree"
        if not safe_repo_url and not safe_repo_path:
            return self._response(
                ok=False,
                summary="repo_workspace prepare failed",
                text="repo_url or repo_path is required",
                error_code="invalid_args",
            )

        source_info = (
            await self._prepare_mirror(safe_repo_url)
            if safe_repo_url
            else await self._prepare_local_source(safe_repo_path)
        )
        if not source_info.get("ok"):
            return self._response(
                ok=False,
                summary="repo_workspace prepare failed",
                text=str(source_info.get("message") or "workspace preparation failed"),
                data={"source": source_info},
                error_code=str(
                    source_info.get("error_code") or "workspace_prepare_failed"
                ),
            )

        repo_slug = str(source_info.get("repo_slug") or "repo").strip() or "repo"
        effective_base_branch = (
            str(base_branch or source_info.get("default_branch") or "main").strip()
            or "main"
        )
        effective_branch_name = str(branch_name or "").strip() or _default_branch_name()

        if safe_mode == "reuse_latest":
            for row in await self._list_states():
                if str(row.get("repo_slug") or "").strip() != repo_slug:
                    continue
                if str(row.get("branch_name") or "").strip() != effective_branch_name:
                    continue
                existing_root = str(row.get("repo_root") or "").strip()
                if existing_root and Path(existing_root).exists():
                    return self._response(
                        ok=True,
                        summary="reused existing workspace",
                        text=f"Workspace ready at {existing_root}",
                        data=row,
                    )

        workspace_id = new_workspace_id()
        target_root = workspace_root(repo_slug, workspace_id)
        source_root = str(source_info.get("source_root") or "").strip()
        if target_root.exists():
            shutil.rmtree(target_root, ignore_errors=True)
        target_root.parent.mkdir(parents=True, exist_ok=True)

        await run_shell("git worktree prune", cwd=source_root)
        start_ref = await self._resolve_start_ref(source_root, effective_base_branch)
        worktree_add = await run_shell(
            (
                f"git worktree add -B {shlex.quote(effective_branch_name)} "
                f"{shlex.quote(str(target_root))} {shlex.quote(start_ref)}"
            ),
            cwd=source_root,
        )
        if not worktree_add.get("ok"):
            shutil.rmtree(target_root, ignore_errors=True)
            return self._response(
                ok=False,
                summary="repo_workspace prepare failed",
                text=str(worktree_add.get("summary") or "git worktree add failed"),
                data={"source": source_info, "worktree_add": worktree_add},
                error_code="worktree_add_failed",
            )

        record = {
            "workspace_id": workspace_id,
            "status": "active",
            "mode": safe_mode,
            "source_type": str(source_info.get("source_type") or "").strip(),
            "repo_slug": repo_slug,
            "repo_url": str(source_info.get("origin_url") or safe_repo_url).strip(),
            "repo_root": str(target_root),
            "source_root": source_root,
            "origin_url": str(source_info.get("origin_url") or "").strip(),
            "owner": str(source_info.get("owner") or "").strip(),
            "repo": str(source_info.get("repo") or "").strip(),
            "base_branch": effective_base_branch,
            "branch_name": effective_branch_name,
            "default_branch": str(source_info.get("default_branch") or "main").strip()
            or "main",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        await self._save_state(record)
        return self._response(
            ok=True,
            summary="workspace prepared",
            text=f"Workspace ready at {target_root}",
            data=record,
        )

    async def inspect(
        self, *, workspace_id: str = "", repo_root: str = ""
    ) -> Dict[str, Any]:
        record = await self._load_state(workspace_id) if workspace_id else None
        safe_repo_root = str(repo_root or (record or {}).get("repo_root") or "").strip()
        if not safe_repo_root:
            return self._response(
                ok=False,
                summary="repo_workspace inspect failed",
                text="workspace_id or repo_root is required",
                error_code="invalid_args",
            )
        if not Path(safe_repo_root).exists():
            return self._response(
                ok=False,
                summary="repo_workspace inspect failed",
                text=f"workspace does not exist: {safe_repo_root}",
                error_code="workspace_not_found",
            )

        branch = await run_shell("git rev-parse --abbrev-ref HEAD", cwd=safe_repo_root)
        status = await run_shell("git status --short --branch", cwd=safe_repo_root)
        origin = await self._origin_url(safe_repo_root)
        branch_name = (
            str(branch.get("stdout") or "").strip() if branch.get("ok") else ""
        )
        status_text = str(status.get("stdout") or status.get("summary") or "").strip()
        dirty_files = _parse_status_files(status_text)
        payload = {
            **dict(record or {}),
            "repo_root": safe_repo_root,
            "branch_name": branch_name
            or str((record or {}).get("branch_name") or "").strip(),
            "origin_url": origin or str((record or {}).get("origin_url") or "").strip(),
            "git_status": status_text,
            "dirty_files": dirty_files,
            "is_dirty": bool(dirty_files),
        }
        text = f"Workspace `{payload.get('workspace_id') or 'direct'}` on branch `{payload.get('branch_name') or 'unknown'}`"
        if dirty_files:
            text += f" has {len(dirty_files)} changed files."
        else:
            text += " is clean."
        return self._response(
            ok=True, summary="workspace inspected", text=text, data=payload
        )

    async def cleanup(self, *, workspace_id: str, force: bool = True) -> Dict[str, Any]:
        record = await self._load_state(workspace_id)
        if not record:
            return self._response(
                ok=False,
                summary="repo_workspace cleanup failed",
                text="workspace not found",
                error_code="workspace_not_found",
            )
        repo_root = str(record.get("repo_root") or "").strip()
        source_root = str(record.get("source_root") or "").strip()
        safe_force = bool(force)

        if (
            repo_root
            and Path(repo_root).exists()
            and source_root
            and Path(source_root).exists()
        ):
            remove = await run_shell(
                f"git worktree remove {'--force ' if safe_force else ''}{shlex.quote(repo_root)}",
                cwd=source_root,
            )
            if not remove.get("ok") and Path(repo_root).exists() and safe_force:
                shutil.rmtree(repo_root, ignore_errors=True)
            await run_shell("git worktree prune", cwd=source_root)
        elif repo_root and Path(repo_root).exists() and safe_force:
            shutil.rmtree(repo_root, ignore_errors=True)

        state_path = workspace_state_path(workspace_id)
        if state_path.exists():
            state_path.unlink(missing_ok=True)
        return self._response(
            ok=True,
            summary="workspace cleaned up",
            text=f"Workspace {workspace_id} removed",
            data={"workspace_id": workspace_id, "repo_root": repo_root},
        )

    async def handle(
        self,
        *,
        action: str = "prepare",
        workspace_id: str = "",
        repo_url: str = "",
        repo_path: str = "",
        repo_root: str = "",
        base_branch: str = "",
        branch_name: str = "",
        mode: str = "fresh_worktree",
        force: bool = True,
    ) -> Dict[str, Any]:
        safe_action = str(action or "prepare").strip().lower() or "prepare"
        if safe_action == "prepare":
            return await self.prepare(
                repo_url=repo_url,
                repo_path=repo_path,
                base_branch=base_branch,
                branch_name=branch_name,
                mode=mode,
            )
        if safe_action == "inspect":
            return await self.inspect(workspace_id=workspace_id, repo_root=repo_root)
        if safe_action == "cleanup":
            return await self.cleanup(workspace_id=workspace_id, force=force)
        return self._response(
            ok=False,
            summary="repo_workspace failed",
            text=f"unsupported repo_workspace action: {safe_action}",
            error_code="unsupported_action",
        )


workspace_session_service = WorkspaceSessionService()
