from __future__ import annotations

import argparse
import asyncio
import json
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
from core.tools.gh_tools import gh_tools

prepare_default_env(REPO_ROOT)


async def execute(ctx, params: dict, runtime=None) -> dict:
    _ = (ctx, runtime)
    return await gh_tools.gh_cli(
        action=str(params.get("action") or "auth_status"),
        hostname=str(params.get("hostname") or "github.com"),
        scopes=params.get("scopes"),
        argv=params.get("argv"),
        cwd=str(params.get("cwd") or ""),
        timeout_sec=params.get("timeout_sec", 120),
        notify_platform=str(params.get("notify_platform") or ""),
        notify_chat_id=str(params.get("notify_chat_id") or ""),
        notify_user_id=str(params.get("notify_user_id") or ""),
    )


def _parse_json_array(raw: str, *, option_name: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except Exception as exc:
        raise SystemExit(f"invalid {option_name} JSON: {exc}") from exc
    if not isinstance(loaded, list):
        raise SystemExit(f"{option_name} must be a JSON array")
    return [str(item).strip() for item in loaded if str(item).strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GitHub CLI skill bridge.")
    add_common_arguments(parser)
    parser.add_argument(
        "action",
        help="gh_cli action: auth_start | auth_status | auth_cancel | exec",
    )
    parser.add_argument("--hostname", default="github.com", help="GitHub hostname")
    parser.add_argument(
        "--scopes",
        default="[]",
        help="JSON array of extra auth scopes",
    )
    parser.add_argument(
        "--argv",
        default="[]",
        help="JSON array of gh argv without the leading gh",
    )
    parser.add_argument("--cwd", default="", help="Optional working directory")
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=120,
        help="Command timeout for action=exec",
    )
    return parser


def _params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return merge_params(
        args,
        {
            "action": str(args.action or "").strip(),
            "hostname": str(args.hostname or "github.com").strip(),
            "scopes": _parse_json_array(
                str(args.scopes or "[]"), option_name="--scopes"
            ),
            "argv": _parse_json_array(str(args.argv or "[]"), option_name="--argv"),
            "cwd": str(args.cwd or "").strip(),
            "timeout_sec": int(args.timeout_sec or 120),
        },
    )


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
