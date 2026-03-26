from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Set

from core.local_file_delivery import send_local_file
from core.skill_tool_handlers import skill_tool_handler_registry
from core.tool_registry import tool_registry

from extension.skills.registry import skill_registry as skill_loader

REPO_ROOT = Path(__file__).resolve().parents[2]

RuntimeToolAllowed = Callable[..., Any]
TodoMarkStep = Callable[[str, str, str], Any]
AppendSessionEvent = Callable[[str], Awaitable[None]]


def _policy_result_allowed(result: Any) -> bool:
    if isinstance(result, tuple):
        if not result:
            return False
        return bool(result[0])
    return bool(result)


class RuntimeToolAssembler:
    """Build function-call tool list from registry + policy."""

    def __init__(
        self,
        *,
        runtime_user_id: str,
        platform_name: str,
        runtime_tool_allowed: RuntimeToolAllowed,
        allowed_skill_names: Set[str] | None = None,
        allowed_tool_names: Set[str] | None = None,
    ):
        self.runtime_user_id = str(runtime_user_id or "")
        self.platform_name = str(platform_name or "")
        self.runtime_tool_allowed = runtime_tool_allowed
        self.allowed_skill_names = (
            {
                str(item or "").strip()
                for item in list(allowed_skill_names or [])
                if str(item or "").strip()
            }
            if allowed_skill_names is not None
            else None
        )
        self.allowed_tool_names = (
            {
                str(item or "").strip()
                for item in list(allowed_tool_names or [])
                if str(item or "").strip()
            }
            if allowed_tool_names is not None
            else None
        )

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
            if _policy_result_allowed(
                self.runtime_tool_allowed(
                    runtime_user_id=self.runtime_user_id,
                    platform=self.platform_name,
                    tool_name=name,
                    kind="tool",
                )
            ):
                filtered.append(item)
        return filtered

    def _is_ikaros_runtime(self) -> bool:
        uid = str(self.runtime_user_id or "").strip().lower()
        platform = str(self.platform_name or "").strip().lower()
        return not uid.startswith("subagent::") and platform != "subagent_kernel"

    def _runtime_role(self) -> str:
        uid = str(self.runtime_user_id or "").strip().lower()
        platform = str(self.platform_name or "").strip().lower()
        if uid.startswith("subagent::") or platform == "subagent_kernel":
            return "subagent"
        return "ikaros"

    def _filter_by_explicit_allowed_names(self, tools: List[Any]) -> List[Any]:
        if self.allowed_tool_names is None:
            return tools
        allowed_names = set(self.allowed_tool_names)
        filtered: List[Any] = []
        for item in tools or []:
            name = self._tool_name(item)
            if name and name in allowed_names:
                filtered.append(item)
        return filtered

    async def assemble(self) -> List[Any]:
        merged_tools: List[Any] = []
        merged_tools.extend(
            tool_registry.get_core_tools(runtime_role=self._runtime_role())
        )
        if self.allowed_skill_names is None or self.allowed_skill_names:
            merged_tools.append(tool_registry.get_load_skill_tool())
        merged_tools.extend(
            tool_registry.get_skill_tools(runtime_role=self._runtime_role())
        )
        merged_tools = self._filter_by_policy(merged_tools)
        return self._filter_by_explicit_allowed_names(merged_tools)


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
    todo_mark_step: TodoMarkStep
    append_session_event: AppendSessionEvent
    available_tool_names: Set[str] = field(default_factory=set)
    allowed_skill_names: Set[str] | None = None
    allowed_tool_names: Set[str] | None = None

    def _runtime_only_allowed_tool_names(self) -> Set[str]:
        allowed: Set[str] = set()
        runtime_role = self._runtime_role()
        if runtime_role != "ikaros":
            return allowed
        for name in tool_registry.get_ikaros_tool_names():
            if _policy_result_allowed(
                self.runtime_tool_allowed(
                    runtime_user_id=self.runtime_user_id,
                    platform=self.platform_name,
                    tool_name=name,
                    kind="tool",
                )
            ):
                allowed.add(name)
        return allowed

    def _policy_allows(self, tool_name: str, *, kind: str = "tool") -> bool:
        return _policy_result_allowed(
            self.runtime_tool_allowed(
                runtime_user_id=self.runtime_user_id,
                platform=self.platform_name,
                tool_name=tool_name,
                kind=kind,
            )
        )

    def set_available_tool_names(self, names: Set[str]) -> None:
        resolved = set(names or set())
        resolved.update(self._runtime_only_allowed_tool_names())
        self.available_tool_names = resolved

    def _extract_user_request(self) -> str:
        message = getattr(self.ctx, "message", None)
        text = str(getattr(message, "text", "") or "").strip()
        if text:
            return text
        user_data = getattr(self.ctx, "user_data", None)
        if isinstance(user_data, dict):
            fallback = str(user_data.get("task_goal") or "").strip()
            if fallback:
                return fallback
        return ""

    def _normalize_runtime_user_for_path(self) -> str:
        runtime_user = str(self.runtime_user_id or "").strip()
        if runtime_user.startswith("subagent::"):
            parts = runtime_user.split("::")
            if len(parts) >= 3:
                candidate = str(parts[2] or "").strip()
                if candidate:
                    return candidate
        return runtime_user

    def _is_ikaros_runtime(self) -> bool:
        uid = str(self.runtime_user_id or "").strip().lower()
        platform = str(self.platform_name or "").strip().lower()
        return not uid.startswith("subagent::") and platform != "subagent_kernel"

    def _runtime_role(self) -> str:
        uid = str(self.runtime_user_id or "").strip().lower()
        platform = str(self.platform_name or "").strip().lower()
        if uid.startswith("subagent::") or platform == "subagent_kernel":
            return "subagent"
        return "ikaros"

    @staticmethod
    def _resolve_repo_path(path: str) -> Path | None:
        raw = str(path or "").strip()
        if not raw:
            return None
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        try:
            return candidate.resolve(strict=False)
        except Exception:
            return None

    @classmethod
    def _is_repo_mutation_path(cls, path: str) -> bool:
        resolved = cls._resolve_repo_path(path)
        if resolved is None:
            return False
        try:
            relative = resolved.relative_to(REPO_ROOT)
        except ValueError:
            return False
        parts = relative.parts
        if not parts:
            return False
        return parts[0] != "data"

    def _inject_runtime_bash_env(self, args: Dict[str, Any]) -> Dict[str, Any]:
        command = str(args.get("command") or "").strip()
        if not command:
            return args

        export_parts: List[str] = []
        runtime_user = self._normalize_runtime_user_for_path()
        msg = getattr(self.ctx, "message", None)
        msg_user = getattr(msg, "user", None)
        msg_chat = getattr(msg, "chat", None)
        user_data = getattr(self.ctx, "user_data", None)
        user_data_obj = user_data if isinstance(user_data, dict) else {}

        platform = str(self.platform_name or "").strip()
        chat_id = str(getattr(msg_chat, "id", "") or "").strip()
        source_user_id = str(getattr(msg_user, "id", "") or "").strip()
        forced_platform = str(
            user_data_obj.get("subagent_delivery_platform") or ""
        ).strip()
        forced_chat_id = str(
            user_data_obj.get("subagent_delivery_chat_id") or ""
        ).strip()

        if forced_platform:
            platform = forced_platform
        if forced_chat_id:
            chat_id = forced_chat_id
        if runtime_user:
            export_parts.append(f"X_BOT_RUNTIME_USER_ID={shlex.quote(runtime_user)}")
        if source_user_id:
            export_parts.append(
                f"X_BOT_RUNTIME_SOURCE_USER_ID={shlex.quote(source_user_id)}"
            )
        if platform:
            export_parts.append(f"X_BOT_RUNTIME_PLATFORM={shlex.quote(platform)}")
        if chat_id:
            export_parts.append(f"X_BOT_RUNTIME_CHAT_ID={shlex.quote(chat_id)}")
        if not export_parts:
            return args

        patched = dict(args or {})
        patched["command"] = f"export {' '.join(export_parts)} && {command}"
        return patched

    def _apply_loaded_skill_bash_context(self, args: Dict[str, Any]) -> Dict[str, Any]:
        command = str(args.get("command") or "").strip()
        if not command:
            return args

        user_data = getattr(self.ctx, "user_data", None)
        if not isinstance(user_data, dict):
            return args

        skill_dir = str(user_data.get("last_loaded_skill_dir") or "").strip()
        entrypoint = str(
            user_data.get("last_loaded_skill_entrypoint") or "scripts/execute.py"
        ).strip()
        if not skill_dir or not entrypoint:
            return args

        try:
            parts = shlex.split(command)
        except Exception:
            return args
        if not parts:
            return args

        relative_candidates = {entrypoint, f"./{entrypoint}"}
        if not any(str(part or "").strip() in relative_candidates for part in parts):
            return args

        patched = dict(args or {})
        patched["cwd"] = skill_dir
        return patched

    def _is_loaded_skill_cli_bash_command(self, command: str) -> bool:
        raw = str(command or "").strip()
        if not raw:
            return False

        user_data = getattr(self.ctx, "user_data", None)
        if not isinstance(user_data, dict):
            return False

        skill_dir = str(user_data.get("last_loaded_skill_dir") or "").strip()
        entrypoint = str(
            user_data.get("last_loaded_skill_entrypoint") or "scripts/execute.py"
        ).strip()
        if not skill_dir or not entrypoint:
            return False

        try:
            parts = shlex.split(raw)
        except Exception:
            parts = []
        if not parts:
            return False

        invokes_python = any(
            str(part or "").strip().startswith("python") for part in parts
        )
        if not invokes_python:
            return False

        normalized_entrypoint = entrypoint.lstrip("./")
        relative_candidates = {normalized_entrypoint, f"./{normalized_entrypoint}"}
        absolute_entrypoint = str((Path(skill_dir) / normalized_entrypoint).resolve())

        if absolute_entrypoint in parts:
            return True
        if any(str(part or "").strip() in relative_candidates for part in parts):
            return True
        if skill_dir in parts and any(
            str(part or "").strip() in relative_candidates for part in parts
        ):
            return True
        return False

    def _resolve_skill_tool_binding(self, tool_name: str) -> Dict[str, Any] | None:
        return tool_registry.get_skill_tool_binding(
            tool_name,
            runtime_role=self._runtime_role(),
        )

    async def _execute_skill_tool_binding(
        self,
        *,
        tool_name: str,
        tool_args: Dict[str, Any],
        started: float,
    ) -> Dict[str, Any] | None:
        binding = self._resolve_skill_tool_binding(tool_name)
        if not binding:
            return None

        result = await skill_tool_handler_registry.dispatch(
            str(binding.get("handler") or "").strip(),
            dispatcher=self,
            args=tool_args,
        )
        return result

    def _should_retry_extension(self, result: Dict[str, Any]) -> bool:
        if not isinstance(result, dict):
            return False
        if bool(result.get("ok")):
            return False
        failure_mode = str(result.get("failure_mode") or "recoverable").strip().lower()
        if failure_mode == "fatal":
            return False

        error_code = str(result.get("error_code") or "").strip().lower()
        if error_code in {"invalid_args", "skill_failed", "workflow_failed"}:
            return True

        return False

    def _is_args_changed(
        self,
        *,
        old_args: Dict[str, Any],
        new_args: Dict[str, Any],
    ) -> bool:
        if set(old_args.keys()) != set(new_args.keys()):
            return True
        for key, value in new_args.items():
            if old_args.get(key) != value:
                return True
        return False

    def _attach_arg_plan(
        self,
        *,
        result: Dict[str, Any],
        plan: Dict[str, Any],
        resolved_args: Dict[str, Any],
        attempt: int,
    ) -> Dict[str, Any]:
        payload = dict(result or {})
        payload["resolved_args"] = resolved_args
        payload["arg_planner"] = {
            "attempt": attempt,
            "planned": bool(plan.get("planned")),
            "source": str(plan.get("source") or ""),
            "missing_fields": list(plan.get("missing_fields") or []),
            "reason": str(plan.get("reason") or "")[:300],
        }
        return payload

    async def execute(
        self,
        *,
        name: str,
        args: Dict[str, Any],
        execution_policy: Any,
        started: float,
    ) -> Dict[str, Any]:
        tool_args = dict(args or {})
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
            if not self._policy_allows(tool_name, kind="tool"):
                blocked = {
                    "ok": False,
                    "error_code": "policy_blocked",
                    "message": f"Tool policy blocked: {tool_name}",
                    "failure_mode": "recoverable",
                }
                return blocked
            normalized_args = dict(args or {})
            if tool_name == "bash":
                normalized_args = dict(normalized_args)
                normalized_args = self._apply_loaded_skill_bash_context(normalized_args)
                normalized_args = self._inject_runtime_bash_env(normalized_args)
            result = await self.tool_broker.execute_core_tool(
                name=tool_name,
                args=normalized_args,
                execution_policy=execution_policy,
                task_workspace_root=self.task_workspace_root,
            )
            self.todo_mark_step("act", "in_progress", f"Tool `{tool_name}` finished.")
            return result

        if tool_name == "send_local_file":
            if self._runtime_role() != "ikaros":
                return {
                    "ok": False,
                    "error_code": "policy_blocked",
                    "message": "Tool policy blocked: send_local_file",
                    "failure_mode": "fatal",
                    "terminal": True,
                    "text": "❌ 当前执行上下文不允许直接向用户发送服务器文件。",
                }
            if not self._policy_allows(tool_name, kind="tool"):
                return {
                    "ok": False,
                    "error_code": "policy_blocked",
                    "message": f"Tool policy blocked: {tool_name}",
                    "failure_mode": "fatal",
                    "terminal": True,
                    "text": "❌ 当前策略不允许直接向用户发送服务器文件。",
                }
            result = await send_local_file(
                self.ctx,
                path=str(tool_args.get("path") or ""),
                caption=str(tool_args.get("caption") or ""),
                filename=str(tool_args.get("filename") or ""),
                kind=str(tool_args.get("kind") or "auto"),
                task_workspace_root=self.task_workspace_root,
            )
            self.todo_mark_step(
                "act", "in_progress", "Tool `send_local_file` finished."
            )
            return result

        if tool_name in {"spawn_subagent", "await_subagents"}:
            if self._runtime_role() != "ikaros":
                return {
                    "ok": False,
                    "error_code": "policy_blocked",
                    "message": f"Tool policy blocked: {tool_name}",
                    "failure_mode": "recoverable",
                }
            from core.subagent_supervisor import subagent_supervisor

            if tool_name == "spawn_subagent":
                return await subagent_supervisor.spawn(
                    ctx=self.ctx,
                    goal=str(tool_args.get("goal") or ""),
                    allowed_tools=list(tool_args.get("allowed_tools") or []),
                    allowed_skills=list(tool_args.get("allowed_skills") or []),
                    mode=str(tool_args.get("mode") or "inline"),
                    timeout_sec=int(tool_args.get("timeout_sec") or 300),
                    parent_task_id=str(self.task_id or ""),
                    parent_task_inbox_id=str(self.task_inbox_id or ""),
                )
            return await subagent_supervisor.await_subagents(
                subagent_ids=list(tool_args.get("subagent_ids") or []),
                wait_policy=str(tool_args.get("wait_policy") or "all"),
            )

        if tool_name == "load_skill":
            skill_name = str(args.get("skill_name") or "").strip()
            if not skill_name:
                result = {
                    "ok": False,
                    "error_code": "missing_arg",
                    "message": "Missing 'skill_name' argument",
                    "failure_mode": "recoverable",
                }
                return result

            skill_info = skill_loader.get_skill(skill_name) or {}
            resolved_skill_name = str(skill_info.get("name") or skill_name).strip()
            if (
                self.allowed_skill_names is not None
                and resolved_skill_name not in self.allowed_skill_names
            ):
                return {
                    "ok": False,
                    "error_code": "skill_not_in_scope",
                    "message": (
                        f"Skill '{resolved_skill_name or skill_name}' is not available "
                        "for this turn."
                    ),
                    "failure_mode": "recoverable",
                }
            canonical_tool_name = (
                f"ext_{resolved_skill_name.replace('-', '_')}"
                if resolved_skill_name
                else ""
            )
            allowed_roles = [
                str(item or "").strip().lower()
                for item in list(skill_info.get("allowed_roles") or [])
                if str(item or "").strip()
            ]
            contract = dict(skill_info.get("contract") or {})
            runtime_target = str(contract.get("runtime_target") or "").strip().lower()

            if canonical_tool_name and not self._policy_allows(
                canonical_tool_name,
                kind="tool",
            ):
                result = {
                    "ok": False,
                    "error_code": "skill_policy_blocked",
                    "message": (
                        f"Skill '{resolved_skill_name or skill_name}' is not allowed "
                        f"in {self._runtime_role()} runtime."
                    ),
                    "failure_mode": "recoverable",
                }
                return result

            if allowed_roles and self._runtime_role() not in allowed_roles:
                result = {
                    "ok": False,
                    "error_code": "skill_role_blocked",
                    "message": (
                        f"Skill '{resolved_skill_name or skill_name}' is not available "
                        f"in {self._runtime_role()} runtime."
                    ),
                    "failure_mode": "recoverable",
                }
                return result

            if self._runtime_role() == "subagent" and runtime_target == "ikaros":
                result = {
                    "ok": False,
                    "error_code": "skill_role_blocked",
                    "message": (
                        f"Skill '{resolved_skill_name or skill_name}' is ikaros-only "
                        "and cannot be loaded in subagent runtime."
                    ),
                    "failure_mode": "recoverable",
                }
                return result

            content = str(skill_info.get("skill_md_content") or "").strip()
            if not content:
                result = {
                    "ok": False,
                    "error_code": "not_found",
                    "message": f"Skill '{skill_name}' not found or has no content.",
                    "failure_mode": "recoverable",
                }
                return result

            skill_dir = str(skill_info.get("skill_dir") or "").strip()
            entrypoint = str(skill_info.get("entrypoint") or "").strip()
            absolute_entrypoint = ""
            if skill_dir and entrypoint:
                absolute_entrypoint = str((Path(skill_dir) / entrypoint).resolve())

            if isinstance(user_data, dict):
                user_data["last_loaded_skill_name"] = skill_name
                user_data["last_loaded_skill_dir"] = skill_dir
                user_data["last_loaded_skill_entrypoint"] = (
                    entrypoint or "scripts/execute.py"
                )

            runtime_note_lines: list[str] = []
            if skill_dir:
                runtime_note_lines.append("## Runtime Execution Context")
                runtime_note_lines.append(f"- Skill directory: `{skill_dir}`")
            if skill_dir and entrypoint:
                runtime_note_lines.append(
                    f"- Preferred bash usage: `cd {skill_dir} && python {entrypoint} ...`"
                )
            if absolute_entrypoint:
                runtime_note_lines.append(
                    f"- Absolute entrypoint: `python {absolute_entrypoint} ...`"
                )
            if contract:
                runtime_note_lines.append(
                    f"- Contract runtime_target: `{str(contract.get('runtime_target') or '').strip()}`"
                )
                runtime_note_lines.append(
                    f"- Contract change_level: `{str(contract.get('change_level') or '').strip()}`"
                )
                runtime_note_lines.append(
                    f"- Contract rollout_target: `{str(contract.get('rollout_target') or '').strip()}`"
                )
            rendered_content = content
            if runtime_note_lines:
                rendered_content = (
                    f"{content}\n\n" + "\n".join(runtime_note_lines)
                ).strip()

            result = {
                "ok": True,
                "content": rendered_content,
                "skill_dir": skill_dir,
                "entrypoint": entrypoint,
                "absolute_entrypoint": absolute_entrypoint,
            }
            self.todo_mark_step(
                "act", "in_progress", f"Tool `load_skill` loaded '{skill_name}'."
            )
            return result

        binding_result = await self._execute_skill_tool_binding(
            tool_name=tool_name,
            tool_args=tool_args,
            started=started,
        )
        if binding_result is not None:
            return binding_result

        if tool_name == "list_extensions":
            # Direct loading from skill_loader instead of using extension_tools
            items = []
            for row in skill_loader.get_skills_summary():
                items.append(
                    {
                        "name": str(row.get("name") or ""),
                        "description": str(row.get("description") or ""),
                        "triggers": list(row.get("triggers") or []),
                    }
                )
            result = {
                "ok": True,
                "extensions": items,
                "summary": f"{len(items)} extension(s) available",
            }
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
        return unknown
