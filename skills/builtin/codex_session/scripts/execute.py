from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
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
from core.tools.codex_tools import codex_tools

prepare_default_env(REPO_ROOT)


async def execute(ctx, params: dict, runtime=None) -> dict:
    _ = (ctx, runtime)
    return await codex_tools.codex_session(
        action=str(params.get("action") or "status"),
        session_id=str(params.get("session_id") or ""),
        workspace_id=str(params.get("workspace_id") or ""),
        cwd=str(params.get("cwd") or ""),
        instruction=str(params.get("instruction") or ""),
        user_reply=str(params.get("user_reply") or ""),
        backend=str(params.get("backend") or "codex"),
        timeout_sec=int(params.get("timeout_sec", 2400) or 2400),
        source=str(params.get("source") or ""),
        skill_name=str(params.get("skill_name") or ""),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex session skill bridge.")
    add_common_arguments(parser)
    parser.add_argument("action", help="start | continue | status | cancel")
    parser.add_argument("--session-id", default="", help="Existing session id")
    parser.add_argument("--workspace-id", default="", help="Prepared workspace id")
    parser.add_argument("--cwd", default="", help="Direct workspace path override")
    parser.add_argument("--instruction", default="", help="Coding instruction")
    parser.add_argument("--user-reply", default="", help="User answer for continue")
    parser.add_argument("--backend", default="codex", help="Coding backend")
    parser.add_argument("--source", default="", help="Optional session source tag")
    parser.add_argument("--skill-name", default="", help="Optional skill name tag")
    parser.add_argument(
        "--timeout-sec", type=int, default=2400, help="Per-round timeout"
    )
    return parser


def _params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return merge_params(
        args,
        {
            "action": str(args.action or "").strip(),
            "session_id": str(args.session_id or "").strip(),
            "workspace_id": str(args.workspace_id or "").strip(),
            "cwd": str(args.cwd or "").strip(),
            "instruction": str(args.instruction or "").strip(),
            "user_reply": str(args.user_reply or "").strip(),
            "backend": str(args.backend or "codex").strip(),
            "source": str(args.source or "").strip(),
            "skill_name": str(args.skill_name or "").strip(),
            "timeout_sec": int(args.timeout_sec or 2400),
        },
    )


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
