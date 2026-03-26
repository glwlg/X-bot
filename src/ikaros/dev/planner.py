from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List


def _slugify(value: str, fallback: str = "task") -> str:
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


def _short(text: str, limit: int = 64) -> str:
    payload = str(text or "").strip()
    if len(payload) <= limit:
        return payload
    return payload[:limit].rstrip()


def _extract_acceptance(body: str) -> List[str]:
    items: List[str] = []
    for line in str(body or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        normalized = raw.lower()
        if normalized.startswith("- [ ]") or normalized.startswith("- [x]"):
            token = raw[5:].strip()
        elif normalized.startswith("- "):
            token = raw[2:].strip()
        else:
            continue
        if token and token not in items:
            items.append(token)
    return items[:8]


class IkarosDevPlanner:
    def build_plan(
        self,
        *,
        requirement: str,
        issue: Dict[str, Any] | None,
        repo_owner: str,
        repo_name: str,
    ) -> Dict[str, Any]:
        issue_payload = dict(issue or {})
        issue_number = int(issue_payload.get("number") or 0)
        issue_title = str(issue_payload.get("title") or "").strip()
        issue_body = str(issue_payload.get("body") or "")
        goal = str(requirement or "").strip() or issue_title
        if not goal:
            goal = "Implement requested project changes"

        acceptance = _extract_acceptance(issue_body)
        if not acceptance:
            acceptance = [
                "Implement code changes that satisfy the requirement",
                "Keep behavior consistent with existing project conventions",
                "Validation commands pass without new failures",
            ]

        branch_slug = _slugify(_short(issue_title or goal, 36), fallback="task")
        if issue_number > 0:
            branch_name = f"issue-{issue_number}-{branch_slug}"
            pr_title = f"Resolve #{issue_number}: {_short(issue_title or goal, 72)}"
            commit_message = f"fix: resolve issue #{issue_number}"
        else:
            stamp = datetime.now().strftime("%Y%m%d")
            branch_name = f"feature-{stamp}-{branch_slug}"
            pr_title = _short(goal, 72)
            commit_message = f"feat: {_short(goal, 50)}"

        repo_hint = (
            f"{str(repo_owner or '').strip()}/{str(repo_name or '').strip()}".strip("/")
        )
        steps = [
            "Analyze repository context and impacted files",
            "Implement or modify code to satisfy the goal",
            "Add or update tests for changed behavior",
            "Run validation and ensure commands pass",
            "Publish branch and create pull request",
        ]

        body_lines = [
            "## Summary",
            f"- Goal: {goal}",
        ]
        if issue_number > 0:
            body_lines.append(f"- Source issue: #{issue_number}")
        if repo_hint:
            body_lines.append(f"- Repository: {repo_hint}")
        body_lines.append("\n## Acceptance")
        for item in acceptance:
            body_lines.append(f"- {item}")

        return {
            "goal": goal,
            "acceptance": acceptance,
            "steps": steps,
            "branch_name": branch_name,
            "commit_message": commit_message,
            "pr_title": pr_title,
            "pr_body": "\n".join(body_lines),
        }


ikaros_dev_planner = IkarosDevPlanner()
