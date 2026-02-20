from __future__ import annotations

from typing import Any, Dict, List

from core.extension_executor import ExtensionExecutor
from core.skill_loader import skill_loader


_executor = ExtensionExecutor()


class ExtensionTools:
    """Unified extension execution facade for manager and workers."""

    async def list_extensions(self) -> Dict[str, Any]:
        items: List[Dict[str, Any]] = []
        for row in skill_loader.get_skills_summary():
            items.append(
                {
                    "name": str(row.get("name") or ""),
                    "description": str(row.get("description") or ""),
                    "triggers": list(row.get("triggers") or []),
                    "input_schema": row.get("input_schema") or {},
                }
            )
        return {
            "ok": True,
            "extensions": items,
            "summary": f"{len(items)} extension(s) available",
        }

    async def run_extension(
        self,
        *,
        skill_name: str,
        args: Dict[str, Any] | None,
        ctx: Any,
        runtime: Any,
    ) -> Dict[str, Any]:
        name = str(skill_name or "").strip()
        if not name:
            return {
                "ok": False,
                "error_code": "invalid_args",
                "message": "skill_name is required",
            }

        result = await _executor.execute(
            skill_name=name,
            args=dict(args or {}),
            ctx=ctx,
            runtime=runtime,
        )
        payload = result.to_tool_response()
        if result.files:
            payload["files"] = result.files
        return payload


extension_tools = ExtensionTools()
