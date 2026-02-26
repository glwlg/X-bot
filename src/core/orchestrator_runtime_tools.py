from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Set

from core.skill_arg_planner import skill_arg_planner
from core.task_inbox import task_inbox
from core.tool_registry import tool_registry
from core.tools.dev_tools import dev_tools
from core.tools.dispatch_tools import dispatch_tools
from core.tools.extension_tools import extension_tools
from services.md_converter import adapt_md_file_for_platform

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

    async def assemble(self, extension_candidates: list[Any]) -> List[Any]:
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

    def set_extension_candidates(self, extension_candidates: list[Any]) -> None:
        self.extension_map = {
            str(getattr(candidate, "tool_name", "") or ""): candidate
            for candidate in (extension_candidates or [])
            if str(getattr(candidate, "tool_name", "") or "").strip()
        }

    def set_available_tool_names(self, names: Set[str]) -> None:
        self.available_tool_names = set(names or set())

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
        if runtime_user.startswith("worker::"):
            parts = runtime_user.split("::")
            if len(parts) >= 3:
                candidate = str(parts[2] or "").strip()
                if candidate:
                    return candidate
        return runtime_user

    def _normalize_legacy_user_path(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        if tool_name not in {"read", "write", "edit"}:
            return args
        if not self._is_manager_runtime():
            return args

        user_id = self._normalize_runtime_user_for_path()
        if not user_id or user_id == "user1":
            return args

        path = str(args.get("path") or "").strip()
        if not path:
            return args

        replacements = (
            ("data/users/user1/", f"data/users/{user_id}/"),
            ("./data/users/user1/", f"./data/users/{user_id}/"),
            ("/app/data/users/user1/", f"/app/data/users/{user_id}/"),
        )

        normalized = path
        for source, target in replacements:
            if normalized.startswith(source):
                normalized = target + normalized[len(source) :]
                break

        if normalized == path:
            return args

        patched = dict(args or {})
        patched["path"] = normalized
        return patched

    def _is_manager_runtime(self) -> bool:
        uid = str(self.runtime_user_id or "").strip().lower()
        platform = str(self.platform_name or "").strip().lower()
        return not uid.startswith("worker::") and platform != "worker_kernel"

    @staticmethod
    def _is_software_delivery_intent(text: str) -> bool:
        raw = str(text or "").strip().lower()
        if not raw:
            return False

        coding_keywords = (
            "software_delivery",
            "software delivery",
            "github",
            "issue",
            "pull request",
            "pr",
            "代码",
            "编码",
            "开发",
            "修复",
            "bug",
            "技能",
            "skill",
            "创建技能",
            "修改技能",
        )
        return any(token in raw for token in coding_keywords)

    @staticmethod
    def _infer_software_delivery_action(
        *,
        requested_action: str,
        user_request: str,
        args: Dict[str, Any],
    ) -> str:
        action = str(requested_action or "").strip().lower() or "run"

        skill_name = str(args.get("skill_name") or "").strip()
        template_kind = str(args.get("template_kind") or "").strip().lower()
        requirement = str(args.get("requirement") or "").strip()
        instruction = str(args.get("instruction") or "").strip()
        text = " ".join(
            [item for item in [user_request, requirement, instruction] if item]
        ).lower()

        is_skill_intent = (
            bool(skill_name)
            or template_kind in {"skill_create", "skill_modify"}
            or "技能" in text
            or "skill" in text
        )

        repo_path = str(args.get("repo_path") or "").strip()
        repo_url = str(args.get("repo_url") or "").strip()
        issue = str(args.get("issue") or "").strip()
        owner = str(args.get("owner") or "").strip()
        repo = str(args.get("repo") or "").strip()
        has_repo_hint = (
            bool(repo_url)
            or bool(issue)
            or bool(owner and repo)
            or (repo_path not in {"", ".", "./"})
        )

        if action in {"skill_create", "skill_modify", "skill_template"}:
            return action
        if action not in {"", "run", "plan"}:
            return action
        if not is_skill_intent or has_repo_hint:
            return action if action in {"plan", "run"} else "run"

        modify_tokens = (
            "修改",
            "修复",
            "排查",
            "优化",
            "fix",
            "modify",
            "update",
            "debug",
        )
        create_tokens = ("创建", "新建", "新增", "create", "build", "开发", "实现")

        if any(token in text for token in modify_tokens):
            return "skill_modify"
        if any(token in text for token in create_tokens):
            return "skill_create"
        if skill_name:
            return "skill_modify"
        return "skill_create"

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

        text = str(result.get("message") or result.get("text") or "").lower().strip()
        hints = ("missing", "required", "请提供", "缺少", "参数", "field")
        return any(token in text for token in hints)

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
            user_request = self._extract_user_request()
            if (
                tool_name == "bash"
                and self._is_manager_runtime()
                and "software_delivery" in self.available_tool_names
                and self._is_software_delivery_intent(user_request)
            ):
                blocked = {
                    "ok": False,
                    "error_code": "software_delivery_required",
                    "message": (
                        "For code/skill troubleshooting in manager runtime, "
                        "use software_delivery directly instead of bash probing."
                    ),
                    "failure_mode": "recoverable",
                }
                self.record_tool_profile(tool_name, blocked, started)
                return blocked

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
            normalized_args = self._normalize_legacy_user_path(
                tool_name=tool_name,
                args=dict(args or {}),
            )
            result = await self.tool_broker.execute_core_tool(
                name=tool_name,
                args=normalized_args,
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
            if self.task_inbox_id:
                dispatched_worker_id = str(result.get("worker_id") or "").strip()
                if dispatched_worker_id:
                    try:
                        await task_inbox.assign_worker(
                            self.task_inbox_id,
                            worker_id=dispatched_worker_id,
                            reason=str(result.get("selection_reason") or ""),
                            manager_id="core-manager",
                        )
                    except Exception:
                        pass
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

        if tool_name == "software_delivery":
            user_request = self._extract_user_request()
            requested_action = self._infer_software_delivery_action(
                requested_action=str(tool_args.get("action") or "run"),
                user_request=user_request,
                args=dict(tool_args),
            )
            requested_requirement = str(tool_args.get("requirement") or "")
            requested_instruction = str(tool_args.get("instruction") or "")
            result = await dev_tools.software_delivery(
                action=requested_action,
                task_id=str(tool_args.get("task_id") or ""),
                requirement=requested_requirement or user_request,
                instruction=requested_instruction
                or requested_requirement
                or user_request,
                issue=str(tool_args.get("issue") or ""),
                repo_path=str(tool_args.get("repo_path") or ""),
                repo_url=str(tool_args.get("repo_url") or ""),
                cwd=str(tool_args.get("cwd") or ""),
                skill_name=str(tool_args.get("skill_name") or ""),
                source=str(tool_args.get("source") or ""),
                template_kind=str(tool_args.get("template_kind") or ""),
                owner=str(tool_args.get("owner") or ""),
                repo=str(tool_args.get("repo") or ""),
                backend=str(tool_args.get("backend") or ""),
                branch_name=str(tool_args.get("branch_name") or ""),
                base_branch=str(tool_args.get("base_branch") or ""),
                commit_message=str(tool_args.get("commit_message") or ""),
                pr_title=str(tool_args.get("pr_title") or ""),
                pr_body=str(tool_args.get("pr_body") or ""),
                timeout_sec=tool_args.get("timeout_sec", 1800),
                validation_commands=tool_args.get("validation_commands"),
                auto_publish=tool_args.get("auto_publish", True),
                auto_push=tool_args.get("auto_push", True),
                auto_pr=tool_args.get("auto_pr", True),
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
                saved_paths = []
                for filename, content in files.items():
                    if isinstance(content, (bytes, bytearray)):
                        adapted_bytes, adapted_name = adapt_md_file_for_platform(
                            file_bytes=bytes(content),
                            filename=str(filename),
                            platform=self.platform_name,
                        )
                        reply_result = await self.ctx.reply_document(
                            document=adapted_bytes, filename=adapted_name
                        )
                        path = getattr(reply_result, "path", "") if reply_result else ""
                        if path:
                            saved_paths.append(path)
                if saved_paths:
                    result["saved_file_paths"] = saved_paths
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
            skill_name = str(getattr(candidate, "name", "") or "")
            user_request = self._extract_user_request()
            current_args = dict(args or {})
            plan = await skill_arg_planner.plan(
                skill_name=skill_name,
                current_args=current_args,
                user_request=user_request,
            )
            resolved_args = dict(plan.get("args") or current_args)
            result = await extension_tools.run_extension(
                skill_name=skill_name,
                args=resolved_args,
                ctx=self.ctx,
                runtime=self.runtime,
            )
            result = self._attach_arg_plan(
                result=result,
                plan=plan,
                resolved_args=resolved_args,
                attempt=1,
            )

            if self._should_retry_extension(result):
                retry_plan = await skill_arg_planner.plan(
                    skill_name=skill_name,
                    current_args=resolved_args,
                    user_request=user_request,
                    validation_error=str(
                        result.get("message") or result.get("summary") or ""
                    ),
                    force=True,
                )
                retry_args = dict(retry_plan.get("args") or resolved_args)
                if self._is_args_changed(old_args=resolved_args, new_args=retry_args):
                    retry_result = await extension_tools.run_extension(
                        skill_name=skill_name,
                        args=retry_args,
                        ctx=self.ctx,
                        runtime=self.runtime,
                    )
                    result = self._attach_arg_plan(
                        result=retry_result,
                        plan=retry_plan,
                        resolved_args=retry_args,
                        attempt=2,
                    )

            files = result.get("files")
            if isinstance(files, dict):
                saved_paths = []
                for filename, content in files.items():
                    if isinstance(content, (bytes, bytearray)):
                        adapted_bytes, adapted_name = adapt_md_file_for_platform(
                            file_bytes=bytes(content),
                            filename=str(filename),
                            platform=self.platform_name,
                        )
                        reply_result = await self.ctx.reply_document(
                            document=adapted_bytes, filename=adapted_name
                        )
                        path = getattr(reply_result, "path", "") if reply_result else ""
                        if path:
                            saved_paths.append(path)
                if saved_paths:
                    result["saved_file_paths"] = saved_paths

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
