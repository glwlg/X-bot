from __future__ import annotations

from typing import Any, Dict

from manager.dev.codex_session_service import codex_session_service


class CodexTools:
    async def codex_session(
        self,
        *,
        action: str = "status",
        session_id: str = "",
        workspace_id: str = "",
        cwd: str = "",
        instruction: str = "",
        user_reply: str = "",
        backend: str = "codex",
        timeout_sec: Any = 2400,
        source: str = "",
        skill_name: str = "",
    ) -> Dict[str, Any]:
        return await codex_session_service.handle(
            action=action,
            session_id=session_id,
            workspace_id=workspace_id,
            cwd=cwd,
            instruction=instruction,
            user_reply=user_reply,
            backend=backend,
            timeout_sec=int(timeout_sec or 2400),
            source=source,
            skill_name=skill_name,
        )


codex_tools = CodexTools()
