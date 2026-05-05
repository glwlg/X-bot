from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from extension.skills.registry import skill_registry as skill_loader


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
    {
        "name": "complete_task",
        "description": (
            "Emit a structured task closure. In task mode, use this instead of replying "
            "directly when the task is done, blocked, waiting for the user, or waiting "
            "for an external condition."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["done", "failed", "partial", "waiting_user", "waiting_external"],
                    "description": "Structured closure state for the current task.",
                },
                "text": {
                    "type": "string",
                    "description": "User-facing final output or blocked/waiting explanation.",
                },
                "summary": {
                    "type": "string",
                    "description": "Optional short summary for task state tracking.",
                },
                "failure_mode": {
                    "type": "string",
                    "enum": ["recoverable", "fatal"],
                    "default": "recoverable",
                    "description": "Failure severity when status=failed.",
                },
                "files": {
                    "type": "array",
                    "description": "Optional user-visible file artifacts to preserve with the result.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "filename": {"type": "string"},
                            "kind": {
                                "type": "string",
                                "enum": ["auto", "document", "photo", "video", "audio"],
                            },
                            "caption": {"type": "string"},
                        },
                        "required": ["path"],
                    },
                },
                "ui": {
                    "type": "object",
                    "description": "Optional UI payload for the current platform.",
                    "properties": {},
                },
                "followup": {
                    "type": "object",
                    "description": "Optional follow-up metadata when status=waiting_external.",
                    "properties": {},
                },
            },
            "required": ["status", "text"],
        },
    },
]

IKAROS_INTERNAL_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "send_local_file",
        "description": (
            "Prepare an existing safe server-side file for user delivery by the final outer handler. "
            "Use this when the user explicitly asks to receive a file, not just its contents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or workspace-relative file path on the server",
                },
                "caption": {
                    "type": "string",
                    "description": "Optional short caption shown with the attachment",
                },
                "filename": {
                    "type": "string",
                    "description": "Optional override filename shown to the user",
                },
                "kind": {
                    "type": "string",
                    "enum": ["auto", "document", "photo", "video", "audio"],
                    "default": "auto",
                    "description": "Preferred delivery type. Use auto unless you need to force document delivery.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "spawn_subagent",
        "description": (
            "Start an internal subagent for a bounded subtask with an explicit tool scope."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Concrete subtask goal for the child agent",
                },
                "allowed_tools": {
                    "type": "array",
                    "description": "Exact tool names the child agent may use",
                    "items": {"type": "string"},
                },
                "allowed_skills": {
                    "type": "array",
                    "description": "Optional skill names the child agent may load",
                    "items": {"type": "string"},
                },
                "mode": {
                    "type": "string",
                    "enum": ["inline", "detached"],
                    "default": "inline",
                    "description": "inline waits in the current turn; detached continues in background",
                },
                "timeout_sec": {
                    "type": "integer",
                    "default": 300,
                    "description": "Execution timeout for the child agent",
                },
            },
            "required": ["goal", "allowed_tools"],
        },
    },
    {
        "name": "await_subagents",
        "description": "Wait for one or more previously started subagents and collect results.",
        "parameters": {
            "type": "object",
            "properties": {
                "subagent_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Subagent ids returned by spawn_subagent",
                },
                "wait_policy": {
                    "type": "string",
                    "enum": ["all", "any", "none"],
                    "default": "all",
                    "description": "all waits for every child up to the default timeout; any waits for the first completion; none only polls current state",
                },
            },
            "required": ["subagent_ids"],
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

    @staticmethod
    def _runtime_roles(runtime_role: str) -> set[str]:
        safe_role = str(runtime_role or "").strip().lower()
        if safe_role:
            return {safe_role}
        return set()

    def get_core_tools(self, *, runtime_role: str = "") -> List[Dict[str, Any]]:
        tools = deepcopy(CORE_TOOLS)
        if str(runtime_role or "").strip().lower() == "ikaros":
            tools.extend(deepcopy(IKAROS_INTERNAL_TOOLS))
        return tools

    def get_load_skill_tool(self) -> Dict[str, Any]:
        return deepcopy(LOAD_SKILL_TOOL)

    def get_skill_tools(self, *, runtime_role: str = "") -> List[Dict[str, Any]]:
        safe_role = str(runtime_role or "").strip().lower()
        allowed_runtime_roles = self._runtime_roles(safe_role)
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
            if (
                allowed_runtime_roles
                and allowed_roles
                and not allowed_runtime_roles.intersection(allowed_roles)
            ):
                continue
            seen.add(name)
            tools.append(
                {
                    "name": name,
                    "description": str(exported.get("description") or "").strip(),
                    "parameters": deepcopy(
                        exported.get("parameters")
                        or {"type": "object", "properties": {}}
                    ),
                }
            )
        tools.sort(key=lambda item: str(item.get("name") or ""))
        return tools

    def get_ikaros_tools(self) -> List[Dict[str, Any]]:
        return self.get_core_tools(runtime_role="ikaros") + self.get_skill_tools(
            runtime_role="ikaros"
        )

    def get_ikaros_tool_names(self) -> List[str]:
        return [
            str(item.get("name") or "").strip() for item in self.get_ikaros_tools()
        ]

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
        allowed_runtime_roles = self._runtime_roles(safe_role)
        exported = skill_loader.get_tool_export(safe_name)
        if not exported:
            return None
        allowed_roles = [
            str(item or "").strip().lower()
            for item in list(exported.get("allowed_roles") or [])
            if str(item or "").strip()
        ]
        if (
            allowed_runtime_roles
            and allowed_roles
            and not allowed_runtime_roles.intersection(allowed_roles)
        ):
            return None
        return deepcopy(exported)

    # Backward-compatible alias.
    def get_all_tools(self) -> List[Dict[str, Any]]:
        return self.get_core_tools()


tool_registry = ToolRegistry()
