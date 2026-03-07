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

from core.tools.dev_tools import dev_tools


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Software delivery bridge for manager development pipeline.",
    )
    parser.add_argument(
        "action",
        help=(
            "software_delivery action: run/read_issue/plan/implement/validate/"
            "publish/status/logs/resume/skill_create/skill_modify/skill_template"
        ),
    )
    parser.add_argument("--task-id", default="", help="Existing task id")
    parser.add_argument("--requirement", default="", help="Requirement summary")
    parser.add_argument("--instruction", default="", help="Concrete instruction")
    parser.add_argument("--issue", default="", help="Issue URL or owner/repo#number")
    parser.add_argument("--repo-path", default="", help="Local repository path")
    parser.add_argument("--repo-url", default="", help="Repository URL")
    parser.add_argument("--cwd", default="", help="Optional working directory")
    parser.add_argument("--skill-name", default="", help="Target skill name")
    parser.add_argument("--source", default="software_delivery_skill", help="Source")
    parser.add_argument("--template-kind", default="", help="Optional template kind")
    parser.add_argument("--owner", default="", help="Repository owner")
    parser.add_argument("--repo", default="", help="Repository name")
    parser.add_argument("--backend", default="", help="Backend override")
    parser.add_argument("--branch-name", default="", help="Branch name")
    parser.add_argument("--base-branch", default="", help="Base branch")
    parser.add_argument("--commit-message", default="", help="Commit message")
    parser.add_argument("--pr-title", default="", help="Pull request title")
    parser.add_argument("--pr-body", default="", help="Pull request body")
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=1800,
        help="Timeout in seconds, default 1800",
    )
    parser.add_argument(
        "--validation-commands",
        default="",
        help="JSON array of validation commands",
    )
    parser.add_argument(
        "--auto-publish",
        choices=("true", "false"),
        default="true",
        help="Whether to auto publish results",
    )
    parser.add_argument(
        "--auto-push",
        choices=("true", "false"),
        default="true",
        help="Whether to auto push",
    )
    parser.add_argument(
        "--auto-pr",
        choices=("true", "false"),
        default="true",
        help="Whether to auto open PR",
    )
    return parser


def _parse_validation_commands(raw: str) -> Any:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        loaded = json.loads(text)
    except Exception as exc:
        raise SystemExit(f"invalid --validation-commands JSON: {exc}") from exc
    if not isinstance(loaded, list):
        raise SystemExit("--validation-commands must be a JSON array")
    return loaded


def _as_bool(raw: str) -> bool:
    return str(raw or "").strip().lower() == "true"


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    result = await dev_tools.software_delivery(
        action=str(args.action or "").strip(),
        task_id=str(args.task_id or "").strip(),
        requirement=str(args.requirement or "").strip(),
        instruction=str(args.instruction or "").strip(),
        issue=str(args.issue or "").strip(),
        repo_path=str(args.repo_path or "").strip(),
        repo_url=str(args.repo_url or "").strip(),
        cwd=str(args.cwd or "").strip(),
        skill_name=str(args.skill_name or "").strip(),
        source=str(args.source or "").strip() or "software_delivery_skill",
        template_kind=str(args.template_kind or "").strip(),
        owner=str(args.owner or "").strip(),
        repo=str(args.repo or "").strip(),
        backend=str(args.backend or "").strip(),
        branch_name=str(args.branch_name or "").strip(),
        base_branch=str(args.base_branch or "").strip(),
        commit_message=str(args.commit_message or "").strip(),
        pr_title=str(args.pr_title or "").strip(),
        pr_body=str(args.pr_body or "").strip(),
        timeout_sec=int(args.timeout_sec or 1800),
        validation_commands=_parse_validation_commands(str(args.validation_commands or "")),
        auto_publish=_as_bool(str(args.auto_publish)),
        auto_push=_as_bool(str(args.auto_push)),
        auto_pr=_as_bool(str(args.auto_pr)),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if bool(result.get("ok", False)) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
