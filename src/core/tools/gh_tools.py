from __future__ import annotations

from typing import Any, Dict

from ikaros.integrations.gh_cli_service import gh_cli_service


class GhTools:
    async def gh_cli(
        self,
        *,
        action: str = "auth_status",
        hostname: str = "github.com",
        scopes: Any = None,
        argv: Any = None,
        cwd: str = "",
        timeout_sec: Any = 120,
        notify_platform: str = "",
        notify_chat_id: str = "",
        notify_user_id: str = "",
    ) -> Dict[str, Any]:
        return await gh_cli_service.handle(
            action=action,
            hostname=hostname,
            scopes=scopes,
            argv=argv,
            cwd=cwd,
            timeout_sec=timeout_sec,
            notify_platform=notify_platform,
            notify_chat_id=notify_chat_id,
            notify_user_id=notify_user_id,
        )


gh_tools = GhTools()
