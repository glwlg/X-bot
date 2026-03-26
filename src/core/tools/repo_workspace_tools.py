from __future__ import annotations

from typing import Any, Dict

from ikaros.dev.workspace_session_service import workspace_session_service


class RepoWorkspaceTools:
    async def repo_workspace(
        self,
        *,
        action: str = "prepare",
        workspace_id: str = "",
        repo_url: str = "",
        repo_path: str = "",
        repo_root: str = "",
        base_branch: str = "",
        branch_name: str = "",
        mode: str = "fresh_worktree",
        force: Any = True,
    ) -> Dict[str, Any]:
        return await workspace_session_service.handle(
            action=action,
            workspace_id=workspace_id,
            repo_url=repo_url,
            repo_path=repo_path,
            repo_root=repo_root,
            base_branch=base_branch,
            branch_name=branch_name,
            mode=mode,
            force=bool(force),
        )


repo_workspace_tools = RepoWorkspaceTools()
