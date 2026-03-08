from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from core.skill_loader import skill_loader


CORE_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "read",
        "description": "Read file content by lines.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "start_line": {
                    "type": "integer",
                    "description": "1-based start line",
                    "default": 1,
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Max number of lines to read",
                    "default": 200,
                },
                "encoding": {
                    "type": "string",
                    "description": "Text encoding",
                    "default": "utf-8",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write",
        "description": "Write or append file content.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "File content"},
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append"],
                    "default": "overwrite",
                },
                "create_parents": {
                    "type": "boolean",
                    "default": True,
                    "description": "Create parent directories when missing",
                },
                "encoding": {
                    "type": "string",
                    "default": "utf-8",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit",
        "description": "Apply deterministic text replacements in a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "edits": {
                    "type": "array",
                    "description": "Ordered edit operations",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_text": {"type": "string"},
                            "new_text": {"type": "string"},
                            "replace_all": {
                                "type": "boolean",
                                "default": False,
                            },
                        },
                        "required": ["old_text", "new_text"],
                    },
                },
                "dry_run": {
                    "type": "boolean",
                    "default": False,
                },
                "encoding": {
                    "type": "string",
                    "default": "utf-8",
                },
            },
            "required": ["path", "edits"],
        },
    },
    {
        "name": "bash",
        "description": "Run a shell command in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory",
                },
                "timeout_sec": {
                    "type": "integer",
                    "default": 60,
                    "description": "Command timeout in seconds",
                },
            },
            "required": ["command"],
        },
    },
]

LOAD_SKILL_TOOL: Dict[str, Any] = {
    "name": "load_skill",
    "description": "Load the full Markdown SOP of a specific skill by name.",
    "parameters": {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "The name of the skill to load (e.g. reminder)",
            },
        },
        "required": ["skill_name"],
    },
}


class ToolRegistry:
    """Build model-visible tool declarations from core primitives and skill metadata."""

    def get_core_tools(self) -> List[Dict[str, Any]]:
        return deepcopy(CORE_TOOLS)

    def get_load_skill_tool(self) -> Dict[str, Any]:
        return deepcopy(LOAD_SKILL_TOOL)

    def get_skill_tools(self, *, runtime_role: str = "") -> List[Dict[str, Any]]:
        safe_role = str(runtime_role or "").strip().lower()
        tools: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for exported in skill_loader.get_tool_exports():
            name = str(exported.get("name") or "").strip()
            if not name or name in seen:
                continue
            allowed_roles = [
                str(item or "").strip().lower()
                for item in list(exported.get("allowed_roles") or [])
                if str(item or "").strip()
            ]
            if safe_role and allowed_roles and safe_role not in allowed_roles:
                continue
            seen.add(name)
            tools.append(
                {
                    "name": name,
                    "description": str(exported.get("description") or "").strip(),
                    "parameters": deepcopy(
                        exported.get("parameters") or {"type": "object", "properties": {}}
                    ),
                }
            )
        tools.sort(key=lambda item: str(item.get("name") or ""))
        return tools

    def get_manager_tools(self) -> List[Dict[str, Any]]:
        return self.get_skill_tools(runtime_role="manager")

    def get_manager_tool_names(self) -> List[str]:
        return [str(item.get("name") or "").strip() for item in self.get_manager_tools()]

    def get_skill_tool_binding(
        self,
        tool_name: str,
        *,
        runtime_role: str = "",
    ) -> Dict[str, Any] | None:
        safe_name = str(tool_name or "").strip()
        if not safe_name:
            return None
        safe_role = str(runtime_role or "").strip().lower()
        exported = skill_loader.get_tool_export(safe_name)
        if not exported:
            return None
        allowed_roles = [
            str(item or "").strip().lower()
            for item in list(exported.get("allowed_roles") or [])
            if str(item or "").strip()
        ]
        if safe_role and allowed_roles and safe_role not in allowed_roles:
            return None
        return deepcopy(exported)

    # Backward-compatible alias.
    def get_all_tools(self) -> List[Dict[str, Any]]:
        return self.get_core_tools()


tool_registry = ToolRegistry()
