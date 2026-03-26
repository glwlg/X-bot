from __future__ import annotations

from typing import Any, Dict

from ikaros.dev.git_ops_service import git_ops_service


class GitTools:
    async def git_ops(
        self,
        *,
        action: str = "status",
        workspace_id: str = "",
        repo_root: str = "",
        mode: str = "working",
        base_branch: str = "",
        message: str = "",
        strategy: str = "auto",
        branch_name: str = "",
        owner: str = "",
        repo: str = "",
    ) -> Dict[str, Any]:
        return await git_ops_service.handle(
            action=action,
            workspace_id=workspace_id,
            repo_root=repo_root,
            mode=mode,
            base_branch=base_branch,
            message=message,
            strategy=strategy,
            branch_name=branch_name,
            owner=owner,
            repo=repo,
        )


git_tools = GitTools()
