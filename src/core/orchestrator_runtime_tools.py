from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Set

from core.tool_registry import tool_registry
from core.tools.dispatch_tools import dispatch_tools
from core.tools.extension_tools import extension_tools

RuntimeToolAllowed = Callable[..., bool]
RecordToolProfile = Callable[[str, Any, float], None]
TodoMarkStep = Callable[[str, str, str], Any]
AppendSessionEvent = Callable[[str], Awaitable[None]]
OnWorkerDispatched = Callable[[str, str], None]


class RuntimeToolAssembler:
    """Build function-call tool list from registry + policy."""

    def __init__(
        self,
        *,
        runtime_user_id: str,
        platform_name: str,
        runtime_tool_allowed: RuntimeToolAllowed,
    ):
        self.runtime_user_id = str(runtime_user_id or "")
        self.platform_name = str(platform_name or "")
        self.runtime_tool_allowed = runtime_tool_allowed

    @staticmethod
    def _tool_name(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("name") or "").strip()
        return str(getattr(item, "name", "") or "").strip()

    def tool_names(self, tools: List[Any]) -> Set[str]:
        names: Set[str] = set()
        for item in tools or []:
            name = self._tool_name(item)
            if name:
                names.add(name)
        return names

    def _filter_by_policy(self, tools: List[Any]) -> List[Any]:
        filtered: List[Any] = []
        seen: Set[str] = set()
        for item in tools or []:
            name = self._tool_name(item)
            if not name or name in seen:
                continue
            seen.add(name)
            if self.runtime_tool_allowed(
                runtime_user_id=self.runtime_user_id,
                platform=self.platform_name,
                tool_name=name,
                kind="tool",
            ):
                filtered.append(item)
        return filtered

    async def assemble(self, extension_candidates: list) -> List[Any]:
        merged_tools: List[Any] = []
        merged_tools.extend(tool_registry.get_core_tools())
        merged_tools.extend(tool_registry.get_manager_tools())
        merged_tools.extend(tool_registry.get_extension_tools(extension_candidates))
        return self._filter_by_policy(merged_tools)


@dataclass
class ToolCallDispatcher:
    """Execute an already-injected tool call with runtime dependencies."""

    runtime_user_id: str
    platform_name: str
    task_id: str
    task_inbox_id: str
    task_workspace_root: str
    ctx: Any
    runtime: Any
    tool_broker: Any
    runtime_tool_allowed: RuntimeToolAllowed
    record_tool_profile: RecordToolProfile
    todo_mark_step: TodoMarkStep
    append_session_event: AppendSessionEvent
    on_worker_dispatched: OnWorkerDispatched | None = None
    extension_map: Dict[str, Any] = field(default_factory=dict)
    available_tool_names: Set[str] = field(default_factory=set)

    def set_extension_candidates(self, extension_candidates: list) -> None:
        self.extension_map = {
            str(getattr(candidate, "tool_name", "") or ""): candidate
            for candidate in (extension_candidates or [])
            if str(getattr(candidate, "tool_name", "") or "").strip()
        }

    def set_available_tool_names(self, names: Set[str]) -> None:
        self.available_tool_names = set(names or set())

    async def execute(
        self,
        *,
        name: str,
        args: Dict[str, Any],
        execution_policy: Any,
        started: float,
    ) -> Dict[str, Any]:
        tool_name = str(name or "").strip()
        if tool_name and tool_name not in self.available_tool_names:
            ext_alias = f"ext_{tool_name}"
            if ext_alias in self.available_tool_names:
                tool_name = ext_alias
        if tool_name not in self.available_tool_names:
            unknown = {
                "ok": False,
                "error_code": "unknown_tool",
                "message": f"Tool not available: {tool_name}",
                "failure_mode": "recoverable",
            }
            self.record_tool_profile(tool_name, unknown, started)
            return unknown

        user_data = getattr(self.ctx, "user_data", None)
        if isinstance(user_data, dict):
            count = int(user_data.get("tool_call_count", 0) or 0)
            user_data["tool_call_count"] = count + 1
            called_names = user_data.get("called_tool_names")
            if not isinstance(called_names, list):
                called_names = []
            called_names.append(tool_name)
            user_data["called_tool_names"] = called_names[-50:]

        if tool_name in {"read", "write", "edit", "bash"}:
            if not self.runtime_tool_allowed(
                runtime_user_id=self.runtime_user_id,
                platform=self.platform_name,
                tool_name=tool_name,
                kind="tool",
            ):
                blocked = {
                    "ok": False,
                    "error_code": "policy_blocked",
                    "message": f"Tool policy blocked: {tool_name}",
                    "failure_mode": "recoverable",
                }
                self.record_tool_profile(tool_name, blocked, started)
                return blocked
            result = await self.tool_broker.execute_core_tool(
                name=tool_name,
                args=args,
                execution_policy=execution_policy,
                task_workspace_root=self.task_workspace_root,
            )
            self.todo_mark_step("act", "in_progress", f"Tool `{tool_name}` finished.")
            self.record_tool_profile(tool_name, result, started)
            return result

        if tool_name == "list_workers":
            result = await dispatch_tools.list_workers()
            self.record_tool_profile(tool_name, result, started)
            return result

        if tool_name == "dispatch_worker":
            metadata = args.get("metadata")
            metadata_obj = dict(metadata) if isinstance(metadata, dict) else {}
            ctx_user_data = getattr(self.ctx, "user_data", None)
            user_data = ctx_user_data if isinstance(ctx_user_data, dict) else {}
            msg = getattr(self.ctx, "message", None)
            msg_user = getattr(msg, "user", None)
            msg_chat = getattr(msg, "chat", None)
            if "user_id" not in metadata_obj:
                metadata_obj["user_id"] = str(getattr(msg_user, "id", "") or "")
            if "chat_id" not in metadata_obj:
                metadata_obj["chat_id"] = str(getattr(msg_chat, "id", "") or "")
            if "platform" not in metadata_obj:
                metadata_obj["platform"] = str(getattr(msg, "platform", "") or "")

            forced_platform = str(
                user_data.get("worker_delivery_platform") or ""
            ).strip()
            forced_chat_id = str(user_data.get("worker_delivery_chat_id") or "").strip()
            if forced_platform:
                metadata_obj["platform"] = forced_platform
            if forced_chat_id:
                metadata_obj["chat_id"] = forced_chat_id

            if "session_id" not in metadata_obj:
                metadata_obj["session_id"] = str(self.task_id or "")
            result = await dispatch_tools.dispatch_worker(
                instruction=str(args.get("instruction") or ""),
                worker_id=str(args.get("worker_id") or ""),
                backend=str(args.get("backend") or ""),
                metadata=metadata_obj,
            )
            if self.on_worker_dispatched is not None:
                self.on_worker_dispatched(
                    str(result.get("worker_id") or "").strip(),
                    str(result.get("worker_name") or "").strip(),
                )
            self.record_tool_profile(tool_name, result, started)
            return result

        if tool_name == "worker_status":
            result = await dispatch_tools.worker_status(
                worker_id=str(args.get("worker_id") or ""),
                limit=int(args.get("limit", 10) or 10),
            )
            self.record_tool_profile(tool_name, result, started)
            return result

        if tool_name == "list_extensions":
            result = await extension_tools.list_extensions()
            self.record_tool_profile(tool_name, result, started)
            return result

        if tool_name == "run_extension":
            extension_args = args.get("args")
            extension_args = extension_args if isinstance(extension_args, dict) else {}
            result = await extension_tools.run_extension(
                skill_name=str(args.get("skill_name") or ""),
                args=extension_args,
                ctx=self.ctx,
                runtime=self.runtime,
            )
            files = result.get("files")
            if isinstance(files, dict):
                for filename, content in files.items():
                    if isinstance(content, (bytes, bytearray)):
                        await self.ctx.reply_document(
                            document=bytes(content), filename=str(filename)
                        )
            self.record_tool_profile(tool_name, result, started)
            return result

        if tool_name in self.extension_map:
            candidate = self.extension_map[tool_name]
            if not self.runtime_tool_allowed(
                runtime_user_id=self.runtime_user_id,
                platform=self.platform_name,
                tool_name=tool_name,
                kind="tool",
            ):
                blocked = {
                    "ok": False,
                    "error_code": "policy_blocked",
                    "message": f"Tool policy blocked: {tool_name}",
                    "failure_mode": "recoverable",
                }
                self.record_tool_profile(tool_name, blocked, started)
                return blocked
            result = await extension_tools.run_extension(
                skill_name=str(getattr(candidate, "name", "") or ""),
                args=args,
                ctx=self.ctx,
                runtime=self.runtime,
            )

            files = result.get("files")
            if isinstance(files, dict):
                for filename, content in files.items():
                    if isinstance(content, (bytes, bytearray)):
                        await self.ctx.reply_document(
                            document=bytes(content), filename=str(filename)
                        )

            candidate_name = str(getattr(candidate, "name", "") or tool_name)
            if result.get("ok"):
                self.todo_mark_step(
                    "act",
                    "in_progress",
                    f"Extension `{candidate_name}` finished.",
                )
            else:
                self.todo_mark_step(
                    "act",
                    "blocked",
                    f"Extension `{candidate_name}` failed: {result.get('message') or result.get('error_code')}",
                )
            await self.append_session_event(
                f"tool_finish:{self.task_id}:{tool_name}:{'ok' if result.get('ok') else 'failed'}"
            )
            self.record_tool_profile(tool_name, result, started)
            return result

        self.todo_mark_step("act", "blocked", f"Unknown tool `{tool_name}`.")
        await self.append_session_event(
            f"tool_finish:{self.task_id}:{tool_name}:unknown_tool"
        )
        unknown = {
            "ok": False,
            "error_code": "unknown_tool",
            "message": f"Unknown tool: {tool_name}",
        }
        self.record_tool_profile(tool_name, unknown, started)
        return unknown
