from typing import Any, Dict, List


class ToolRegistry:
    """Build model-visible tool declarations."""

    MANAGER_TOOL_NAMES = (
        "list_workers",
        "dispatch_worker",
        "worker_status",
        "software_delivery",
    )

    def get_core_tools(self) -> List[Dict[str, Any]]:
        return [
            self._read_tool(),
            self._write_tool(),
            self._edit_tool(),
            self._bash_tool(),
        ]

    def get_manager_tools(self) -> List[Dict[str, Any]]:
        return []

    def get_manager_tool_names(self) -> List[str]:
        return [name for name in self.MANAGER_TOOL_NAMES]

    def get_load_skill_tool(self) -> Dict[str, Any]:
        return {
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

    def _software_delivery_tool(self) -> Dict[str, Any]:
        return {
            "name": "software_delivery",
            "description": (
                "Manager software delivery pipeline. "
                "Use to read GitHub issues, plan implementation, run coding backend, "
                "validate changes, and publish commit/PR results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "run | read_issue | plan | implement | validate | publish | status | logs | resume | skill_create | skill_modify | skill_template"
                        ),
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Existing software delivery task id",
                    },
                    "requirement": {
                        "type": "string",
                        "description": "Development requirement or feature description",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "Explicit coding instruction, mainly for template actions",
                    },
                    "issue": {
                        "type": "string",
                        "description": "GitHub issue URL or owner/repo#number",
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Local repository path",
                    },
                    "repo_url": {
                        "type": "string",
                        "description": "Git repository URL for clone/pull",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for template coding actions",
                    },
                    "skill_name": {
                        "type": "string",
                        "description": "Target skill name for template actions",
                    },
                    "source": {
                        "type": "string",
                        "description": "Trace source label for coding execution",
                    },
                    "template_kind": {
                        "type": "string",
                        "description": "When action=skill_template, choose skill_create or skill_modify",
                    },
                    "owner": {
                        "type": "string",
                        "description": "GitHub owner override",
                    },
                    "repo": {
                        "type": "string",
                        "description": "GitHub repo override",
                    },
                    "backend": {
                        "type": "string",
                        "description": "Coding backend: codex or gemini-cli",
                    },
                    "branch_name": {
                        "type": "string",
                        "description": "Target branch for implementation",
                    },
                    "base_branch": {
                        "type": "string",
                        "description": "Base branch for publish/PR",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Commit message override",
                    },
                    "pr_title": {
                        "type": "string",
                        "description": "Pull request title override",
                    },
                    "pr_body": {
                        "type": "string",
                        "description": "Pull request body override",
                    },
                    "timeout_sec": {
                        "type": "integer",
                        "description": "Timeout for coding execution",
                        "default": 1800,
                    },
                    "validation_commands": {
                        "type": "array",
                        "description": "Optional validation command list",
                        "items": {"type": "string"},
                    },
                    "auto_publish": {
                        "type": "boolean",
                        "description": "When action=run/resume, include publish stage",
                        "default": True,
                    },
                    "auto_push": {
                        "type": "boolean",
                        "description": "Push branch before PR",
                        "default": True,
                    },
                    "auto_pr": {
                        "type": "boolean",
                        "description": "Create pull request after push",
                        "default": True,
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
