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

try:
    from .service import git_ops_service
except ImportError:
    from service import git_ops_service

prepare_default_env(REPO_ROOT)


async def execute(ctx, params: dict, runtime=None) -> dict:
    _ = (ctx, runtime)
    return await git_ops_service.handle(
        action=str(params.get("action") or "status"),
        workspace_id=str(params.get("workspace_id") or ""),
        repo_root=str(params.get("repo_root") or ""),
        mode=str(params.get("mode") or "working"),
        base_branch=str(params.get("base_branch") or ""),
        message=str(params.get("message") or ""),
        strategy=str(params.get("strategy") or "auto"),
        branch_name=str(params.get("branch_name") or ""),
        owner=str(params.get("owner") or ""),
        repo=str(params.get("repo") or ""),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Git ops skill bridge.")
    add_common_arguments(parser)
    parser.add_argument("action", help="status | diff | branches | commit | push")
    parser.add_argument("--workspace-id", default="", help="Prepared workspace id")
    parser.add_argument("--repo-root", default="", help="Direct repository root")
    parser.add_argument("--mode", default="working", help="Diff mode")
    parser.add_argument("--base-branch", default="", help="Base branch")
    parser.add_argument("--message", default="", help="Commit message")
    parser.add_argument("--strategy", default="auto", help="Push strategy")
    parser.add_argument("--branch-name", default="", help="Branch override")
    parser.add_argument("--owner", default="", help="Upstream owner")
    parser.add_argument("--repo", default="", help="Upstream repo")
    return parser


def _params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return merge_params(
        args,
        {
            "action": str(args.action or "").strip(),
            "workspace_id": str(args.workspace_id or "").strip(),
            "repo_root": str(args.repo_root or "").strip(),
            "mode": str(args.mode or "working").strip(),
            "base_branch": str(args.base_branch or "").strip(),
            "message": str(args.message or "").strip(),
            "strategy": str(args.strategy or "auto").strip(),
            "branch_name": str(args.branch_name or "").strip(),
            "owner": str(args.owner or "").strip(),
            "repo": str(args.repo or "").strip(),
        },
    )


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
