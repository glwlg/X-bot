import inspect
from typing import Any, Dict

from core.primitive_runtime import PrimitiveRuntime


class ToolBroker:
    """Route core tool calls with execution policies."""

    def __init__(self, runtime: PrimitiveRuntime | None = None):
        self.runtime = runtime or PrimitiveRuntime()

    @staticmethod
    def resolve_policy(ctx: Any) -> str:
        try:
            policy = str((ctx.user_data or {}).get("execution_policy", "")).strip()
        except Exception:
            policy = ""
        return policy or "worker_execution_policy"

    async def execute_core_tool(
        self,
        *,
        name: str,
        args: Dict[str, Any],
        execution_policy: str,
        task_workspace_root: str = "",
    ) -> Dict[str, Any]:
        if name == "read":
            resolved_path = self._normalize_task_path(args.get("path", ""), task_workspace_root)
            return await self.runtime.read(
                path=resolved_path,
                start_line=int(args.get("start_line", 1)),
                max_lines=int(args.get("max_lines", 200)),
                encoding=args.get("encoding", "utf-8"),
            )
        if name == "write":
            resolved_path = self._normalize_task_path(args.get("path", ""), task_workspace_root)
            payload = {
                "path": resolved_path,
                "content": args.get("content", ""),
                "mode": args.get("mode", "overwrite"),
                "create_parents": bool(args.get("create_parents", True)),
                "encoding": args.get("encoding", "utf-8"),
            }
            if self._accepts_kwarg(self.runtime.write, "execution_policy"):
                payload["execution_policy"] = execution_policy
            return await self.runtime.write(
                **payload
            )
        if name == "edit":
            resolved_path = self._normalize_task_path(args.get("path", ""), task_workspace_root)
            payload = {
                "path": resolved_path,
                "edits": args.get("edits", []),
                "dry_run": bool(args.get("dry_run", False)),
                "encoding": args.get("encoding", "utf-8"),
            }
            if self._accepts_kwarg(self.runtime.edit, "execution_policy"):
                payload["execution_policy"] = execution_policy
            return await self.runtime.edit(**payload)
        if name == "bash":
            resolved_cwd = self._normalize_task_cwd(args.get("cwd"), task_workspace_root)
            payload = {
                "command": args.get("command", ""),
                "cwd": resolved_cwd,
                "timeout_sec": int(args.get("timeout_sec", 60)),
            }
            if self._accepts_kwarg(self.runtime.bash, "execution_policy"):
                payload["execution_policy"] = execution_policy
            return await self.runtime.bash(**payload)
        return {
            "ok": False,
            "error_code": "unknown_tool",
            "message": f"Unknown core tool: {name}",
        }

    @staticmethod
    def _accepts_kwarg(fn: Any, key: str) -> bool:
        try:
            signature = inspect.signature(fn)
        except Exception:
            return False
        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                return True
        return key in signature.parameters

    @staticmethod
    def _normalize_task_path(path: str, task_workspace_root: str = "") -> str:
        import os

        raw = str(path or "").strip()
        if not raw or not task_workspace_root:
            return raw
        expanded = os.path.expanduser(raw)
        if os.path.isabs(expanded):
            return expanded
        return os.path.abspath(os.path.join(task_workspace_root, expanded))

    @staticmethod
    def _normalize_task_cwd(cwd: str | None, task_workspace_root: str = "") -> str | None:
        import os

        raw = str(cwd).strip() if cwd is not None else ""
        if raw:
            if not task_workspace_root:
                return raw
            expanded = os.path.expanduser(raw)
            if os.path.isabs(expanded):
                return expanded
            return os.path.abspath(os.path.join(task_workspace_root, expanded))
        if task_workspace_root:
            return task_workspace_root
        return None


tool_broker = ToolBroker()
