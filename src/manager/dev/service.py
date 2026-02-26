from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List

from manager.dev.planner import manager_dev_planner
from manager.dev.publisher import manager_dev_publisher
from manager.dev.runtime import run_coding_backend
from manager.dev.task_store import dev_task_store
from manager.dev.validator import manager_dev_validator
from manager.dev.workspace import dev_workspace_manager
from manager.integrations.github_client import GitHubClientError, github_client


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _clean_list(value: Any) -> List[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        rows = [str(item).strip() for item in value if str(item).strip()]
        return rows or None
    if isinstance(value, str):
        rows = [item.strip() for item in value.split("\n") if item.strip()]
        return rows or None
    return None


def _to_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return int(default)


def _short(text: str, limit: int = 240) -> str:
    payload = str(text or "").strip()
    if len(payload) <= limit:
        return payload
    return payload[:limit].rstrip() + "..."


def _sanitize_skill_name(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if not raw:
        return ""
    safe_chars = [ch if (ch.isalnum() or ch == "_") else "_" for ch in raw]
    token = "".join(safe_chars)
    while "__" in token:
        token = token.replace("__", "_")
    token = token.strip("_")
    if not token:
        return ""
    if token[0].isdigit():
        token = f"skill_{token}"
    return token[:64]


class ManagerDevService:
    def __init__(self) -> None:
        self.github = github_client
        self.tasks = dev_task_store
        self.workspace = dev_workspace_manager
        self.planner = manager_dev_planner
        self.validator = manager_dev_validator
        self.publisher = manager_dev_publisher

    def _response(
        self,
        *,
        ok: bool,
        summary: str,
        task_id: str = "",
        status: str = "",
        text: str = "",
        data: Dict[str, Any] | None = None,
        error_code: str = "",
        terminal: bool = False,
        task_outcome: str = "",
        failure_mode: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": bool(ok),
            "summary": str(summary or "").strip(),
            "task_id": str(task_id or "").strip(),
            "status": str(status or "").strip(),
            "text": str(text or summary or "").strip(),
            "data": dict(data or {}),
            "terminal": bool(terminal),
        }
        if not ok:
            payload["error_code"] = str(
                error_code or "software_delivery_failed"
            ).strip()
            payload["message"] = str(text or summary or "operation failed").strip()
            payload["failure_mode"] = str(failure_mode or "fatal").strip() or "fatal"
        if terminal:
            payload["task_outcome"] = str(task_outcome or "").strip().lower() or (
                "done" if ok else "failed"
            )
        elif task_outcome:
            payload["task_outcome"] = str(task_outcome).strip().lower()
        return payload

    def _append_event(
        self,
        record: Dict[str, Any],
        *,
        name: str,
        detail: str,
        data: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        events = list(record.get("events") or [])
        event: Dict[str, Any] = {
            "at": _now_iso(),
            "name": str(name or "").strip(),
            "detail": _short(detail, 600),
        }
        if isinstance(data, dict) and data:
            event["data"] = data
        events.append(event)
        record["events"] = events[-80:]
        record["updated_at"] = _now_iso()
        return record

    async def _load_task(self, task_id: str) -> Dict[str, Any] | None:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return None
        return await self.tasks.load(safe_task_id)

    def _build_instruction(self, record: Dict[str, Any]) -> str:
        goal = str(record.get("goal") or record.get("requirement") or "").strip()
        issue_payload = dict(record.get("issue") or {})
        plan_payload = dict(record.get("plan") or {})
        acceptance = [
            str(item).strip()
            for item in list(plan_payload.get("acceptance") or [])
            if str(item).strip()
        ]
        steps = [
            str(item).strip()
            for item in list(plan_payload.get("steps") or [])
            if str(item).strip()
        ]

        sections: List[str] = []
        sections.append("Implement software changes directly in this repository.")
        sections.append(f"Primary goal: {goal}")

        issue_number = int(issue_payload.get("number") or 0)
        if issue_number > 0:
            issue_lines = [
                f"Issue #{issue_number}: {str(issue_payload.get('title') or '').strip()}",
                _short(str(issue_payload.get("body") or ""), 3000),
            ]
            comments = list(issue_payload.get("comments") or [])
            if comments:
                rendered_comments: List[str] = []
                for row in comments[:6]:
                    if not isinstance(row, dict):
                        continue
                    author = str(row.get("user") or "").strip()
                    body = _short(str(row.get("body") or ""), 500)
                    if body:
                        rendered_comments.append(f"- {author}: {body}")
                if rendered_comments:
                    issue_lines.append(
                        "Issue comments:\n" + "\n".join(rendered_comments)
                    )
            sections.append("\n".join([item for item in issue_lines if item]))

        if steps:
            sections.append(
                "Execution steps:\n" + "\n".join([f"- {item}" for item in steps])
            )
        if acceptance:
            sections.append(
                "Acceptance criteria:\n"
                + "\n".join([f"- {item}" for item in acceptance])
            )

        sections.append(
            "Required output:\n"
            "- Modify project files to implement the goal\n"
            "- Update or add tests when behavior changes\n"
            "- Keep changes minimal and aligned with existing conventions"
        )
        return "\n\n".join([item for item in sections if item])

    async def _fetch_issue_if_needed(
        self,
        *,
        issue: str,
        owner: str,
        repo: str,
    ) -> Dict[str, Any]:
        safe_issue = str(issue or "").strip()
        if not safe_issue:
            return {}
        return await self.github.fetch_issue(
            safe_issue,
            default_owner=str(owner or "").strip(),
            default_repo=str(repo or "").strip(),
        )

    async def read_issue(
        self,
        *,
        issue: str,
        owner: str = "",
        repo: str = "",
    ) -> Dict[str, Any]:
        try:
            payload = await self._fetch_issue_if_needed(
                issue=issue,
                owner=owner,
                repo=repo,
            )
        except GitHubClientError as exc:
            return self._response(
                ok=False,
                summary="read_issue failed",
                text=str(exc),
                error_code="read_issue_failed",
            )

        return self._response(
            ok=True,
            summary=f"Issue loaded: #{int(payload.get('number') or 0)}",
            text=f"Issue loaded: {str(payload.get('title') or '').strip()}",
            data={"issue": payload},
        )

    async def plan(
        self,
        *,
        requirement: str,
        issue: str = "",
        repo_path: str = "",
        repo_url: str = "",
        owner: str = "",
        repo: str = "",
        base_branch: str = "",
    ) -> Dict[str, Any]:
        workspace = await self.workspace.prepare_workspace(
            repo_path=repo_path,
            repo_url=repo_url,
            owner=owner,
            repo=repo,
        )
        if not workspace.get("ok"):
            return self._response(
                ok=False,
                summary="plan failed",
                text=str(workspace.get("message") or "workspace resolution failed"),
                error_code=str(workspace.get("error_code") or "workspace_failed"),
                data={"workspace": workspace},
            )

        issue_payload: Dict[str, Any] = {}
        if str(issue or "").strip():
            try:
                issue_payload = await self._fetch_issue_if_needed(
                    issue=issue,
                    owner=str(workspace.get("owner") or owner or ""),
                    repo=str(workspace.get("repo") or repo or ""),
                )
            except GitHubClientError as exc:
                return self._response(
                    ok=False,
                    summary="plan failed",
                    text=str(exc),
                    error_code="issue_fetch_failed",
                )

        plan_payload = self.planner.build_plan(
            requirement=str(requirement or "").strip(),
            issue=issue_payload,
            repo_owner=str(workspace.get("owner") or ""),
            repo_name=str(workspace.get("repo") or ""),
        )

        git_payload = {
            "branch_name": str(plan_payload.get("branch_name") or "").strip(),
            "base_branch": str(
                base_branch or workspace.get("default_branch") or "main"
            ).strip()
            or "main",
            "commit_message": str(plan_payload.get("commit_message") or "").strip(),
            "pr_title": str(plan_payload.get("pr_title") or "").strip(),
            "pr_body": str(plan_payload.get("pr_body") or "").strip(),
        }

        record = {
            "status": "planned",
            "requirement": str(requirement or "").strip(),
            "goal": str(plan_payload.get("goal") or "").strip(),
            "issue_ref": str(issue or "").strip(),
            "issue": issue_payload,
            "repo": {
                "path": str(workspace.get("path") or "").strip(),
                "url": str(workspace.get("origin_url") or repo_url or "").strip(),
                "owner": str(workspace.get("owner") or "").strip(),
                "name": str(workspace.get("repo") or "").strip(),
                "default_branch": str(
                    workspace.get("default_branch") or "main"
                ).strip(),
            },
            "plan": {
                "steps": list(plan_payload.get("steps") or []),
                "acceptance": list(plan_payload.get("acceptance") or []),
            },
            "git": git_payload,
            "implementation": {},
            "validation": {},
            "publish": {},
            "events": [],
            "error": "",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        self._append_event(
            record,
            name="plan_created",
            detail=f"Plan created for goal: {record['goal']}",
        )
        saved = await self.tasks.create(record)

        return self._response(
            ok=True,
            summary="development plan created",
            task_id=str(saved.get("task_id") or "").strip(),
            status=str(saved.get("status") or "planned"),
            text=f"Plan ready: {saved.get('goal')}",
            data={
                "goal": saved.get("goal"),
                "plan": saved.get("plan"),
                "repo": saved.get("repo"),
                "git": saved.get("git"),
            },
        )

    async def implement(
        self,
        *,
        task_id: str,
        backend: str = "",
        branch_name: str = "",
        base_branch: str = "",
        timeout_sec: int = 2400,
    ) -> Dict[str, Any]:
        record = await self._load_task(task_id)
        if not record:
            return self._response(
                ok=False,
                summary="implement failed",
                text="task not found",
                error_code="task_not_found",
            )

        repo_payload = dict(record.get("repo") or {})
        git_payload = dict(record.get("git") or {})
        safe_repo_path = str(repo_payload.get("path") or "").strip()

        branch_result = await self.workspace.ensure_branch(
            repo_path=safe_repo_path,
            branch_name=str(
                branch_name or git_payload.get("branch_name") or ""
            ).strip(),
            base_branch=str(
                base_branch or git_payload.get("base_branch") or ""
            ).strip(),
        )
        if not branch_result.get("ok"):
            record["status"] = "failed"
            record["error"] = str(
                branch_result.get("message") or "failed to prepare branch"
            )
            self._append_event(
                record,
                name="implement_failed",
                detail=record["error"],
                data={"stage": "ensure_branch"},
            )
            await self.tasks.save(record)
            return self._response(
                ok=False,
                summary="implement failed",
                task_id=str(record.get("task_id") or "").strip(),
                status=str(record.get("status") or "failed"),
                text=record["error"],
                error_code="ensure_branch_failed",
                data={"branch": branch_result},
            )

        git_payload["branch_name"] = str(branch_result.get("branch_name") or "").strip()
        if str(branch_result.get("base_branch") or "").strip():
            git_payload["base_branch"] = str(
                branch_result.get("base_branch") or ""
            ).strip()
        record["git"] = git_payload
        record["status"] = "implementing"
        record["error"] = ""
        self._append_event(
            record,
            name="implement_started",
            detail=f"backend={backend or os.getenv('CODING_BACKEND_DEFAULT', 'codex')}",
        )
        await self.tasks.save(record)

        instruction = self._build_instruction(record)
        result = await run_coding_backend(
            instruction=instruction,
            backend=str(backend or "").strip(),
            cwd=safe_repo_path,
            timeout_sec=max(300, int(timeout_sec or 2400)),
            source="manager_dev_service",
        )

        record["implementation"] = {
            "backend": str(result.get("backend") or "").strip(),
            "result": result,
        }
        if not result.get("ok"):
            record["status"] = "failed"
            record["error"] = str(result.get("summary") or result.get("message") or "")
            self._append_event(
                record,
                name="implement_failed",
                detail=record["error"],
                data={"error_code": str(result.get("error_code") or "")},
            )
            await self.tasks.save(record)
            return self._response(
                ok=False,
                summary="implementation failed",
                task_id=str(record.get("task_id") or "").strip(),
                status="failed",
                text=record["error"],
                error_code=str(result.get("error_code") or "implementation_failed"),
                data={"implementation": record.get("implementation")},
            )

        record["status"] = "implemented"
        record["error"] = ""
        self._append_event(
            record,
            name="implement_done",
            detail=_short(str(result.get("summary") or "implemented"), 500),
        )
        await self.tasks.save(record)
        return self._response(
            ok=True,
            summary="implementation completed",
            task_id=str(record.get("task_id") or "").strip(),
            status="implemented",
            text=str(result.get("summary") or "implementation completed"),
            data={"implementation": record.get("implementation")},
            task_outcome="partial",
        )

    async def validate(
        self,
        *,
        task_id: str,
        validation_commands: List[str] | None = None,
        timeout_sec: int = 1800,
    ) -> Dict[str, Any]:
        record = await self._load_task(task_id)
        if not record:
            return self._response(
                ok=False,
                summary="validate failed",
                text="task not found",
                error_code="task_not_found",
            )

        repo_payload = dict(record.get("repo") or {})
        safe_repo_path = str(repo_payload.get("path") or "").strip()
        record["status"] = "validating"
        record["error"] = ""
        self._append_event(record, name="validate_started", detail="running validation")
        await self.tasks.save(record)

        result = await self.validator.validate(
            repo_path=safe_repo_path,
            validation_commands=validation_commands,
            timeout_sec=max(60, int(timeout_sec or 1800)),
        )
        record["validation"] = result

        if not result.get("ok"):
            record["status"] = "failed"
            record["error"] = str(result.get("summary") or "validation failed")
            self._append_event(
                record,
                name="validate_failed",
                detail=record["error"],
            )
            await self.tasks.save(record)
            return self._response(
                ok=False,
                summary="validation failed",
                task_id=str(record.get("task_id") or "").strip(),
                status="failed",
                text=record["error"],
                error_code="validation_failed",
                data={"validation": result},
            )

        record["status"] = "validated"
        record["error"] = ""
        self._append_event(
            record,
            name="validate_done",
            detail=str(result.get("summary") or "validation passed"),
        )
        await self.tasks.save(record)
        return self._response(
            ok=True,
            summary="validation passed",
            task_id=str(record.get("task_id") or "").strip(),
            status="validated",
            text=str(result.get("summary") or "validation passed"),
            data={"validation": result},
            task_outcome="partial",
        )

    async def publish(
        self,
        *,
        task_id: str,
        commit_message: str = "",
        pr_title: str = "",
        pr_body: str = "",
        base_branch: str = "",
        auto_push: bool = True,
        auto_pr: bool = True,
    ) -> Dict[str, Any]:
        record = await self._load_task(task_id)
        if not record:
            return self._response(
                ok=False,
                summary="publish failed",
                text="task not found",
                error_code="task_not_found",
                terminal=True,
                task_outcome="failed",
            )

        repo_payload = dict(record.get("repo") or {})
        git_payload = dict(record.get("git") or {})

        safe_repo_path = str(repo_payload.get("path") or "").strip()
        safe_owner = str(repo_payload.get("owner") or "").strip()
        safe_repo = str(repo_payload.get("name") or "").strip()
        safe_branch_name = str(git_payload.get("branch_name") or "").strip()
        safe_base_branch = (
            str(base_branch or git_payload.get("base_branch") or "").strip() or "main"
        )
        safe_commit_message = (
            str(commit_message or git_payload.get("commit_message") or "").strip()
            or "chore: update project"
        )
        safe_pr_title = (
            str(pr_title or git_payload.get("pr_title") or "").strip()
            or safe_commit_message
        )
        safe_pr_body = str(pr_body or git_payload.get("pr_body") or "").strip()

        record["status"] = "publishing"
        record["error"] = ""
        self._append_event(record, name="publish_started", detail="publishing changes")
        await self.tasks.save(record)

        publish_result = await self.publisher.publish(
            repo_path=safe_repo_path,
            owner=safe_owner,
            repo=safe_repo,
            branch_name=safe_branch_name,
            base_branch=safe_base_branch,
            commit_message=safe_commit_message,
            pr_title=safe_pr_title,
            pr_body=safe_pr_body,
            auto_push=bool(auto_push),
            auto_pr=bool(auto_pr),
        )
        record["publish"] = publish_result

        if not publish_result.get("ok"):
            record["status"] = "failed"
            record["error"] = str(publish_result.get("message") or "publish failed")
            self._append_event(
                record,
                name="publish_failed",
                detail=record["error"],
            )
            await self.tasks.save(record)
            return self._response(
                ok=False,
                summary="publish failed",
                task_id=str(record.get("task_id") or "").strip(),
                status="failed",
                text=record["error"],
                error_code=str(publish_result.get("error_code") or "publish_failed"),
                data={"publish": publish_result},
                terminal=True,
                task_outcome="failed",
            )

        issue_payload = dict(record.get("issue") or {})
        issue_number = int(issue_payload.get("number") or 0)
        pr_url = str(
            (publish_result.get("pull_request") or {}).get("html_url") or ""
        ).strip()
        if issue_number > 0 and pr_url:
            try:
                comment = await self.github.create_issue_comment(
                    owner=safe_owner,
                    repo=safe_repo,
                    issue_number=issue_number,
                    body=(
                        "Automated update from manager software delivery pipeline.\n\n"
                        f"Pull request: {pr_url}"
                    ),
                )
                record["issue_comment"] = comment
                self._append_event(
                    record,
                    name="issue_commented",
                    detail=f"Issue #{issue_number} commented with PR link",
                )
            except GitHubClientError as exc:
                record["issue_comment_error"] = str(exc)
                self._append_event(
                    record,
                    name="issue_comment_failed",
                    detail=str(exc),
                )

        record["status"] = "done"
        record["error"] = ""
        self._append_event(
            record,
            name="publish_done",
            detail="publish completed",
            data={"pr_url": pr_url},
        )
        await self.tasks.save(record)
        return self._response(
            ok=True,
            summary="publish completed",
            task_id=str(record.get("task_id") or "").strip(),
            status="done",
            text=str(pr_url or "Publish completed"),
            data={"publish": publish_result, "pr_url": pr_url},
            terminal=True,
            task_outcome="done",
        )

    async def run_skill_template(
        self,
        *,
        action: str,
        instruction: str,
        cwd: str,
        backend: str = "",
        skill_name: str = "",
        source: str = "",
        timeout_sec: int = 1800,
    ) -> Dict[str, Any]:
        safe_action = str(action or "").strip().lower()
        if safe_action not in {"skill_create", "skill_modify"}:
            return self._response(
                ok=False,
                summary="skill template failed",
                text="unsupported skill template action",
                error_code="invalid_args",
                terminal=True,
                task_outcome="failed",
            )

        safe_skill_name = _sanitize_skill_name(skill_name)
        safe_instruction = self._fallback_template_instruction(
            action=safe_action,
            instruction=instruction,
            skill_name=safe_skill_name,
        )
        safe_cwd = str(cwd or "").strip() or self._resolve_skill_template_cwd(
            action=safe_action,
            skill_name=safe_skill_name,
        )

        if safe_action == "skill_modify" and not safe_cwd:
            return self._response(
                ok=False,
                summary="skill template failed",
                text="skill_modify 需要有效的 skill_name（且目标技能必须存在且非 builtin）",
                error_code="invalid_args",
                terminal=True,
                task_outcome="failed",
            )

        if not safe_instruction:
            return self._response(
                ok=False,
                summary="skill template failed",
                text="instruction is required",
                error_code="invalid_args",
                terminal=True,
                task_outcome="failed",
            )

        if not safe_cwd:
            return self._response(
                ok=False,
                summary="skill template failed",
                text="cwd is required",
                error_code="invalid_args",
                terminal=True,
                task_outcome="failed",
            )

        if safe_action == "skill_create":
            os.makedirs(safe_cwd, exist_ok=True)

        run_result = await run_coding_backend(
            instruction=safe_instruction,
            backend=str(backend or "").strip(),
            cwd=safe_cwd,
            timeout_sec=max(60, int(timeout_sec or 1800)),
            source=str(source or f"software_delivery_{safe_action}").strip(),
        )

        backend_used = str(run_result.get("backend") or backend or "").strip()
        data = {
            "mode": "skill_template",
            "template_action": safe_action,
            "skill_name": safe_skill_name,
            "cwd": safe_cwd,
            "backend": backend_used,
            "source": str(source or f"software_delivery_{safe_action}").strip(),
            "template_result": run_result,
        }

        if not run_result.get("ok"):
            message = str(
                run_result.get("summary")
                or run_result.get("message")
                or "skill template execution failed"
            )
            return self._response(
                ok=False,
                summary="skill template failed",
                text=message,
                data=data,
                error_code=str(run_result.get("error_code") or "skill_template_failed"),
                terminal=True,
                task_outcome="failed",
            )

        summary = str(run_result.get("summary") or "skill template execution completed")
        return self._response(
            ok=True,
            summary=summary,
            text=summary,
            data=data,
            terminal=True,
            task_outcome="done",
        )

    def _fallback_template_instruction(
        self,
        *,
        action: str,
        instruction: str,
        skill_name: str,
    ) -> str:
        safe_instruction = str(instruction or "").strip()
        if safe_instruction:
            return safe_instruction

        safe_skill = str(skill_name or "").strip()
        if action == "skill_modify":
            if safe_skill:
                return (
                    f"请修改技能 `{safe_skill}`，根据用户请求更新能力，"
                    "并保持 SKILL.md 与 scripts 结构可加载。"
                )
            return ""

        if safe_skill:
            return (
                f"请创建技能 `{safe_skill}`，根据用户请求实现能力，"
                "并生成有效的 SKILL.md。"
            )
        return "请根据用户请求创建一个新技能，并生成有效的 SKILL.md。"

    def _resolve_skill_template_cwd(self, *, action: str, skill_name: str) -> str:
        safe_action = str(action or "").strip().lower()
        safe_skill = _sanitize_skill_name(skill_name)

        try:
            from core.skill_loader import skill_loader

            skills_root = str(getattr(skill_loader, "skills_dir", "") or "").strip()
            if not skills_root:
                skills_root = os.path.abspath(os.path.join(os.getcwd(), "skills"))

            if safe_action == "skill_modify":
                if not safe_skill:
                    return ""
                info = skill_loader.get_skill(safe_skill) or {}
                if str(info.get("source") or "").strip() == "builtin":
                    return ""
                target = str(info.get("skill_dir") or "").strip()
                if target:
                    return target
                return ""

            target_name = (
                safe_skill or f"skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            return os.path.abspath(os.path.join(skills_root, "learned", target_name))
        except Exception:
            fallback_root = os.path.abspath(
                os.path.join(os.getcwd(), "skills", "learned")
            )
            target_name = (
                safe_skill or f"skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            return os.path.abspath(os.path.join(fallback_root, target_name))

    async def status(self, *, task_id: str) -> Dict[str, Any]:
        record = await self._load_task(task_id)
        if not record:
            return self._response(
                ok=False,
                summary="status failed",
                text="task not found",
                error_code="task_not_found",
            )

        summary = f"task {record.get('task_id')} status: {record.get('status')}"
        return self._response(
            ok=True,
            summary=summary,
            task_id=str(record.get("task_id") or "").strip(),
            status=str(record.get("status") or "").strip(),
            text=summary,
            data={"task": record},
        )

    async def resume(
        self,
        *,
        task_id: str,
        backend: str = "",
        commit_message: str = "",
        pr_title: str = "",
        pr_body: str = "",
        base_branch: str = "",
        validation_commands: List[str] | None = None,
        auto_publish: bool = True,
        auto_push: bool = True,
        auto_pr: bool = True,
    ) -> Dict[str, Any]:
        record = await self._load_task(task_id)
        if not record:
            return self._response(
                ok=False,
                summary="resume failed",
                text="task not found",
                error_code="task_not_found",
                terminal=True,
                task_outcome="failed",
            )

        status = str(record.get("status") or "").strip().lower()
        if status in {"done", "failed"}:
            return await self.status(task_id=task_id)

        if status in {"planned", "implementing", "implemented"}:
            impl = await self.implement(task_id=task_id, backend=backend)
            if not impl.get("ok"):
                impl["terminal"] = True
                impl["task_outcome"] = "failed"
                return impl

        status_check = await self.status(task_id=task_id)
        next_status = str(status_check.get("status") or "").strip().lower()
        if next_status in {"implemented", "validating", "validated"}:
            val = await self.validate(
                task_id=task_id,
                validation_commands=validation_commands,
            )
            if not val.get("ok"):
                val["terminal"] = True
                val["task_outcome"] = "failed"
                return val

        if not auto_publish:
            done = await self.status(task_id=task_id)
            done["terminal"] = True
            done["task_outcome"] = "done"
            return done

        pub = await self.publish(
            task_id=task_id,
            commit_message=commit_message,
            pr_title=pr_title,
            pr_body=pr_body,
            base_branch=base_branch,
            auto_push=auto_push,
            auto_pr=auto_pr,
        )
        return pub

    async def run(
        self,
        *,
        task_id: str = "",
        requirement: str = "",
        issue: str = "",
        repo_path: str = "",
        repo_url: str = "",
        owner: str = "",
        repo: str = "",
        backend: str = "",
        branch_name: str = "",
        base_branch: str = "",
        commit_message: str = "",
        pr_title: str = "",
        pr_body: str = "",
        validation_commands: List[str] | None = None,
        auto_publish: bool = True,
        auto_push: bool = True,
        auto_pr: bool = True,
    ) -> Dict[str, Any]:
        safe_task_id = str(task_id or "").strip()
        if safe_task_id:
            return await self.resume(
                task_id=safe_task_id,
                backend=backend,
                commit_message=commit_message,
                pr_title=pr_title,
                pr_body=pr_body,
                base_branch=base_branch,
                validation_commands=validation_commands,
                auto_publish=auto_publish,
                auto_push=auto_push,
                auto_pr=auto_pr,
            )

        planned = await self.plan(
            requirement=requirement,
            issue=issue,
            repo_path=repo_path,
            repo_url=repo_url,
            owner=owner,
            repo=repo,
            base_branch=base_branch,
        )
        if not planned.get("ok"):
            planned["terminal"] = True
            planned["task_outcome"] = "failed"
            return planned

        created_task_id = str(planned.get("task_id") or "").strip()
        impl = await self.implement(
            task_id=created_task_id,
            backend=backend,
            branch_name=branch_name,
            base_branch=base_branch,
        )
        if not impl.get("ok"):
            impl["terminal"] = True
            impl["task_outcome"] = "failed"
            return impl

        val = await self.validate(
            task_id=created_task_id,
            validation_commands=validation_commands,
        )
        if not val.get("ok"):
            val["terminal"] = True
            val["task_outcome"] = "failed"
            return val

        if not auto_publish:
            done = await self.status(task_id=created_task_id)
            done["terminal"] = True
            done["task_outcome"] = "done"
            return done

        pub = await self.publish(
            task_id=created_task_id,
            commit_message=commit_message,
            pr_title=pr_title,
            pr_body=pr_body,
            base_branch=base_branch,
            auto_push=auto_push,
            auto_pr=auto_pr,
        )
        return pub

    async def software_delivery(
        self,
        *,
        action: str = "run",
        task_id: str = "",
        requirement: str = "",
        instruction: str = "",
        issue: str = "",
        repo_path: str = "",
        repo_url: str = "",
        cwd: str = "",
        skill_name: str = "",
        source: str = "",
        template_kind: str = "",
        owner: str = "",
        repo: str = "",
        backend: str = "",
        branch_name: str = "",
        base_branch: str = "",
        commit_message: str = "",
        pr_title: str = "",
        pr_body: str = "",
        timeout_sec: Any = 1800,
        validation_commands: Any = None,
        auto_publish: Any = True,
        auto_push: Any = True,
        auto_pr: Any = True,
    ) -> Dict[str, Any]:
        safe_action = str(action or "run").strip().lower() or "run"
        safe_validation_commands = _clean_list(validation_commands)
        safe_auto_publish = _as_bool(auto_publish, default=True)
        safe_auto_push = _as_bool(auto_push, default=True)
        safe_auto_pr = _as_bool(auto_pr, default=True)
        safe_timeout_sec = max(60, _to_int(timeout_sec, 1800))

        try:
            if safe_action == "read_issue":
                return await self.read_issue(issue=issue, owner=owner, repo=repo)
            if safe_action == "plan":
                return await self.plan(
                    requirement=requirement,
                    issue=issue,
                    repo_path=repo_path,
                    repo_url=repo_url,
                    owner=owner,
                    repo=repo,
                    base_branch=base_branch,
                )
            if safe_action == "implement":
                return await self.implement(
                    task_id=task_id,
                    backend=backend,
                    branch_name=branch_name,
                    base_branch=base_branch,
                )
            if safe_action == "validate":
                return await self.validate(
                    task_id=task_id,
                    validation_commands=safe_validation_commands,
                )
            if safe_action == "publish":
                return await self.publish(
                    task_id=task_id,
                    commit_message=commit_message,
                    pr_title=pr_title,
                    pr_body=pr_body,
                    base_branch=base_branch,
                    auto_push=safe_auto_push,
                    auto_pr=safe_auto_pr,
                )
            if safe_action == "status":
                return await self.status(task_id=task_id)
            if safe_action == "resume":
                return await self.resume(
                    task_id=task_id,
                    backend=backend,
                    commit_message=commit_message,
                    pr_title=pr_title,
                    pr_body=pr_body,
                    base_branch=base_branch,
                    validation_commands=safe_validation_commands,
                    auto_publish=safe_auto_publish,
                    auto_push=safe_auto_push,
                    auto_pr=safe_auto_pr,
                )
            if safe_action in {"skill_create", "skill_modify", "skill_template"}:
                resolved_template_action = safe_action
                if safe_action == "skill_template":
                    candidate = str(template_kind or "").strip().lower()
                    if candidate in {"skill_create", "skill_modify"}:
                        resolved_template_action = candidate
                    else:
                        resolved_template_action = "skill_modify"
                return await self.run_skill_template(
                    action=resolved_template_action,
                    instruction=str(instruction or requirement).strip(),
                    cwd=str(cwd or repo_path).strip(),
                    backend=backend,
                    skill_name=skill_name,
                    source=source,
                    timeout_sec=safe_timeout_sec,
                )
            return await self.run(
                task_id=task_id,
                requirement=requirement,
                issue=issue,
                repo_path=repo_path,
                repo_url=repo_url,
                owner=owner,
                repo=repo,
                backend=backend,
                branch_name=branch_name,
                base_branch=base_branch,
                commit_message=commit_message,
                pr_title=pr_title,
                pr_body=pr_body,
                validation_commands=safe_validation_commands,
                auto_publish=safe_auto_publish,
                auto_push=safe_auto_push,
                auto_pr=safe_auto_pr,
            )
        except GitHubClientError as exc:
            return self._response(
                ok=False,
                summary="software_delivery failed",
                text=str(exc),
                error_code="github_error",
                terminal=True,
                task_outcome="failed",
            )
        except Exception as exc:
            return self._response(
                ok=False,
                summary="software_delivery failed",
                text=str(exc),
                error_code="software_delivery_failed",
                terminal=True,
                task_outcome="failed",
            )


manager_dev_service = ManagerDevService()
