from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "src"
os.environ.setdefault("DATA_DIR", str((REPO_ROOT / "data").resolve()))
os.environ.setdefault(
    "MANAGER_DISPATCH_ROOT",
    str((REPO_ROOT / "data" / "system" / "dispatch").resolve()),
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.tools.dispatch_tools import dispatch_tools


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Worker management bridge for list/status/dispatch actions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List workers and capabilities")

    dispatch_parser = subparsers.add_parser(
        "dispatch",
        help="Dispatch a concrete instruction to a worker",
    )
    dispatch_parser.add_argument("instruction", help="Task instruction for the worker")
    dispatch_parser.add_argument("--worker-id", default="", help="Optional worker id")
    dispatch_parser.add_argument("--backend", default="", help="Optional backend")
    dispatch_parser.add_argument(
        "--source",
        default="worker_management_skill",
        help="Trace source label",
    )
    dispatch_parser.add_argument(
        "--metadata",
        default="{}",
        help="Optional JSON object metadata",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Show recent worker task status/history",
    )
    status_parser.add_argument("--worker-id", default="", help="Optional worker id")
    status_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Recent task limit, default 10",
    )
    return parser


def _parse_metadata(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except Exception as exc:
        raise SystemExit(f"invalid --metadata JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SystemExit("--metadata must be a JSON object")
    return loaded


def _apply_runtime_delivery_defaults(metadata: dict[str, Any]) -> dict[str, Any]:
    merged = dict(metadata or {})
    runtime_platform = str(os.getenv("X_BOT_RUNTIME_PLATFORM", "") or "").strip()
    runtime_chat_id = str(os.getenv("X_BOT_RUNTIME_CHAT_ID", "") or "").strip()
    runtime_source_user_id = str(
        os.getenv("X_BOT_RUNTIME_SOURCE_USER_ID", "") or ""
    ).strip()
    runtime_user_id = str(os.getenv("X_BOT_RUNTIME_USER_ID", "") or "").strip()

    if runtime_platform and not str(merged.get("platform") or "").strip():
        merged["platform"] = runtime_platform
    if runtime_chat_id and not str(merged.get("chat_id") or "").strip():
        merged["chat_id"] = runtime_chat_id

    effective_user_id = runtime_source_user_id or runtime_user_id
    if effective_user_id and not str(merged.get("user_id") or "").strip():
        merged["user_id"] = effective_user_id
    return merged


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    command = str(args.command or "").strip().lower()

    if command == "list":
        result = await dispatch_tools.list_workers()
    elif command == "dispatch":
        metadata = _apply_runtime_delivery_defaults(
            _parse_metadata(str(args.metadata or "{}"))
        )
        result = await dispatch_tools.dispatch_worker(
            instruction=str(args.instruction or "").strip(),
            worker_id=str(args.worker_id or "").strip(),
            backend=str(args.backend or "").strip(),
            source=str(args.source or "").strip() or "worker_management_skill",
            metadata=metadata,
        )
    elif command == "status":
        result = await dispatch_tools.worker_status(
            worker_id=str(args.worker_id or "").strip(),
            limit=max(1, min(50, int(args.limit or 10))),
        )
    else:
        parser.error(f"unsupported command: {command}")
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if bool(result.get("ok", False)) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
