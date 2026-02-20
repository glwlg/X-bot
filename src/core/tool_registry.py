from typing import Any, Dict, List

from core.extension_router import ExtensionCandidate
from core.tool_profile_store import tool_profile_store


class ToolRegistry:
    """Build model-visible tool declarations."""

    def get_core_tools(self) -> List[Dict[str, Any]]:
        return [
            self._read_tool(),
            self._write_tool(),
            self._edit_tool(),
            self._bash_tool(),
        ]

    def get_manager_tools(self) -> List[Dict[str, Any]]:
        return [
            self._list_workers_tool(),
            self._dispatch_worker_tool(),
            self._worker_status_tool(),
        ]

    def get_extension_tools(
        self, candidates: List[ExtensionCandidate]
    ) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for candidate in candidates:
            schema = candidate.input_schema or {"type": "object", "properties": {}}
            if "type" not in schema:
                schema["type"] = "object"
            trigger_hint = (
                ", ".join(candidate.triggers[:6]) if candidate.triggers else "none"
            )
            allowed_tools = [
                str(item).strip()
                for item in list(getattr(candidate, "allowed_tools", []) or [])
                if str(item).strip()
            ]
            allowed_hint = ", ".join(allowed_tools[:6]) if allowed_tools else "none"
            profile = tool_profile_store.get_profile(candidate.tool_name)
            attempts = int(profile.get("attempts", 0) or 0)
            avg_latency = float(profile.get("avg_latency_ms", 0.0) or 0.0)
            successes = int(profile.get("successes", 0) or 0)
            success_rate = (successes / attempts) if attempts > 0 else 0.0
            profile_hint = f"profile(success_rate={success_rate:.2f}, avg_latency_ms={avg_latency:.1f}, attempts={attempts})"
            desc = (
                f"On-demand extension: {candidate.name}. "
                f"{candidate.description}\n"
                f"Triggers: {trigger_hint}\n"
                f"Allowed runtime tools: {allowed_hint}\n"
                f"Input schema summary: {candidate.schema_summary}\n"
                f"Capability: {profile_hint}\n"
                "Prefer core tools (`read`/`write`/`edit`/`bash`) first; "
                "use this extension only when it is clearly more suitable or user explicitly requests it. "
                "Avoid asking clarification unless required fields are missing."
            )
            tools.append(
                {
                    "name": candidate.tool_name,
                    "description": desc,
                    "parameters": schema,
                }
            )
        return tools

    # Backward-compatible alias.
    def get_all_tools(self) -> List[Dict[str, Any]]:
        return self.get_core_tools()

    def _read_tool(self) -> Dict[str, Any]:
        return {
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
        }

    def _write_tool(self) -> Dict[str, Any]:
        return {
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
        }

    def _edit_tool(self) -> Dict[str, Any]:
        return {
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
        }

    def _bash_tool(self) -> Dict[str, Any]:
        return {
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
        }

    def _list_workers_tool(self) -> Dict[str, Any]:
        return {
            "name": "list_workers",
            "description": "List worker instances and their capabilities/status.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        }

    def _dispatch_worker_tool(self) -> Dict[str, Any]:
        return {
            "name": "dispatch_worker",
            "description": (
                "Dispatch a concrete execution task to a worker. "
                "Use when command execution, long-running operations, or specialized execution is needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "Task instruction for worker execution",
                    },
                    "worker_id": {
                        "type": "string",
                        "description": "Optional target worker id. Omit to auto-select.",
                    },
                    "backend": {
                        "type": "string",
                        "description": "Optional backend override when worker policy allows it",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional metadata for traceability",
                    },
                },
                "required": ["instruction"],
            },
        }

    def _worker_status_tool(self) -> Dict[str, Any]:
        return {
            "name": "worker_status",
            "description": "Query recent worker execution status and task history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "worker_id": {
                        "type": "string",
                        "description": "Optional worker id",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Recent task limit (1-50)",
                        "default": 10,
                    },
                },
            },
        }

    def _list_extensions_tool(self) -> Dict[str, Any]:
        return {
            "name": "list_extensions",
            "description": "List all available extensions/skills and their input schemas.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        }

    def _run_extension_tool(self) -> Dict[str, Any]:
        return {
            "name": "run_extension",
            "description": (
                "Execute one extension by skill name with args. "
                "Use this as the generic extension invocation path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Exact skill name, e.g. rss_subscribe / stock_watch",
                    },
                    "args": {
                        "type": "object",
                        "description": "Extension input args object",
                    },
                },
                "required": ["skill_name", "args"],
            },
        }


tool_registry = ToolRegistry()
