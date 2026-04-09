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
from extension.skills.runtime_context import notify_target, runtime_user_id, task_inbox_id

try:
    from .service import task_tracker_service
except ImportError:
    from service import task_tracker_service

prepare_default_env(REPO_ROOT)


async def execute(ctx, params: dict, runtime=None) -> dict:
    _ = runtime
    target = notify_target(ctx, params)
    return await task_tracker_service.handle(
        action=str(params.get("action") or "list_open"),
        user_id=str(params.get("user_id") or runtime_user_id(ctx, params)),
        task_id=str(params.get("task_id") or task_inbox_id(params)),
        limit=int(params.get("limit", 20) or 20),
        due_only=bool(params.get("due_only", True)),
        event_limit=int(params.get("event_limit", 20) or 20),
        status=str(params.get("status") or ""),
        result_summary=str(params.get("result_summary") or ""),
        done_when=str(params.get("done_when") or ""),
        next_review_after=str(params.get("next_review_after") or ""),
        refs=dict(params.get("refs") or {}) if isinstance(params.get("refs"), dict) else {},
        notes=str(params.get("notes") or ""),
        announce_before_action=params.get("announce_before_action"),
        last_observation=str(params.get("last_observation") or ""),
        last_action_summary=str(params.get("last_action_summary") or ""),
        announce_text=str(params.get("announce_text") or ""),
        announce_key=str(params.get("announce_key") or ""),
        announce_platform=str(
            params.get("announce_platform") or target.get("notify_platform") or ""
        ),
        announce_chat_id=str(
            params.get("announce_chat_id") or target.get("notify_chat_id") or ""
        ),
    )


def _parse_json_object(raw: str, *, option_name: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except Exception as exc:
        raise SystemExit(f"invalid {option_name} JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SystemExit(f"{option_name} must be a JSON object")
    return dict(loaded)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Task tracker skill.")
    add_common_arguments(parser)
    parser.add_argument("action", help="list_open | get | update")
    parser.add_argument("--user-id", default="", help="Runtime user id")
    parser.add_argument("--task-id", default="", help="Task inbox id")
    parser.add_argument("--limit", type=int, default=20, help="List size")
    parser.add_argument("--due-only", action="store_true", help="Only include due tasks")
    parser.add_argument("--event-limit", type=int, default=20, help="Event count")
    parser.add_argument("--status", default="", help="Updated task status")
    parser.add_argument("--result-summary", default="", help="User-visible summary")
    parser.add_argument("--done-when", default="", help="Completion contract")
    parser.add_argument(
        "--next-review-after",
        default="",
        help="Next review timestamp in ISO format",
    )
    parser.add_argument("--refs", default="{}", help="JSON object refs")
    parser.add_argument("--notes", default="", help="Persistent notes")
    parser.add_argument(
        "--announce-before-action",
        default="",
        help="true | false, whether follow-up should announce itself",
    )
    parser.add_argument("--last-observation", default="", help="Last observation")
    parser.add_argument(
        "--last-action-summary",
        default="",
        help="Last action summary",
    )
    parser.add_argument("--announce-text", default="", help="Optional proactive text")
    parser.add_argument("--announce-key", default="", help="Optional dedupe key")
    return parser


def _params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    announce_before_action: bool | None
    raw_announce = str(args.announce_before_action or "").strip().lower()
    if raw_announce in {"true", "1", "yes", "on"}:
        announce_before_action = True
    elif raw_announce in {"false", "0", "no", "off"}:
        announce_before_action = False
    else:
        announce_before_action = None
    return merge_params(
        args,
        {
            "action": str(args.action or "").strip(),
            "user_id": str(args.user_id or "").strip(),
            "task_id": str(args.task_id or "").strip(),
            "limit": int(args.limit or 20),
            "due_only": bool(args.due_only),
            "event_limit": int(args.event_limit or 20),
            "status": str(args.status or "").strip(),
            "result_summary": str(args.result_summary or "").strip(),
            "done_when": str(args.done_when or "").strip(),
            "next_review_after": str(args.next_review_after or "").strip(),
            "refs": _parse_json_object(str(args.refs or "{}"), option_name="--refs"),
            "notes": str(args.notes or "").strip(),
            "announce_before_action": announce_before_action,
            "last_observation": str(args.last_observation or "").strip(),
            "last_action_summary": str(args.last_action_summary or "").strip(),
            "announce_text": str(args.announce_text or "").strip(),
            "announce_key": str(args.announce_key or "").strip(),
        },
    )


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
