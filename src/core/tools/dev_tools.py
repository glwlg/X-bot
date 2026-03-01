from __future__ import annotations

from typing import Any, Dict

from manager.dev.service import manager_dev_service


class DevTools:
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
        return await manager_dev_service.software_delivery(
            action=action,
            task_id=task_id,
            requirement=requirement,
            instruction=instruction,
            issue=issue,
            repo_path=repo_path,
            repo_url=repo_url,
            cwd=cwd,
            skill_name=skill_name,
            source=source,
            template_kind=template_kind,
            owner=owner,
            repo=repo,
            backend=backend,
            branch_name=branch_name,
            base_branch=base_branch,
            commit_message=commit_message,
            pr_title=pr_title,
            pr_body=pr_body,
            timeout_sec=timeout_sec,
            validation_commands=validation_commands,
            auto_publish=auto_publish,
            auto_push=auto_push,
            auto_pr=auto_pr,
        )


dev_tools = DevTools()
