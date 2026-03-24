from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)
from core.tools.repo_workspace_tools import repo_workspace_tools

prepare_default_env(REPO_ROOT)


async def execute(ctx, params: dict, runtime=None) -> dict:
    _ = (ctx, runtime)
    return await repo_workspace_tools.repo_workspace(
        action=str(params.get("action") or "prepare"),
        workspace_id=str(params.get("workspace_id") or ""),
        repo_url=str(params.get("repo_url") or ""),
        repo_path=str(params.get("repo_path") or ""),
        repo_root=str(params.get("repo_root") or ""),
        base_branch=str(params.get("base_branch") or ""),
        branch_name=str(params.get("branch_name") or ""),
        mode=str(params.get("mode") or "fresh_worktree"),
        force=bool(params.get("force", True)),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repo workspace skill bridge.")
    add_common_arguments(parser)
    parser.add_argument("action", help="prepare | inspect | cleanup")
    parser.add_argument("--workspace-id", default="", help="Existing workspace id")
    parser.add_argument("--repo-url", default="", help="Repository URL")
    parser.add_argument("--repo-path", default="", help="Local repository path")
    parser.add_argument("--repo-root", default="", help="Direct workspace root")
    parser.add_argument("--base-branch", default="", help="Base branch")
    parser.add_argument("--branch-name", default="", help="Session branch")
    parser.add_argument(
        "--mode", default="fresh_worktree", help="fresh_worktree | reuse_latest"
    )
    parser.add_argument("--force", action="store_true", help="Force cleanup")
    return parser


def _params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return merge_params(
        args,
        {
            "action": str(args.action or "").strip(),
            "workspace_id": str(args.workspace_id or "").strip(),
            "repo_url": str(args.repo_url or "").strip(),
            "repo_path": str(args.repo_path or "").strip(),
            "repo_root": str(args.repo_root or "").strip(),
            "base_branch": str(args.base_branch or "").strip(),
            "branch_name": str(args.branch_name or "").strip(),
            "mode": str(args.mode or "fresh_worktree").strip(),
            "force": bool(args.force),
        },
    )


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
