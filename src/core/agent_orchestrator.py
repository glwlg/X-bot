import asyncio
import datetime
import logging
import os
import re
import time
from typing import Any, Dict, List

from core.config import (
    DATA_DIR,
    MCP_MEMORY_ENABLED,
    X_DEPLOYMENT_STAGING_PATH,
    SERVER_IP,
    AUTO_RECOVERY_MAX_ATTEMPTS,
)
from core.extension_executor import ExtensionExecutor
from core.extension_router import ExtensionCandidate, ExtensionRouter
from core.heartbeat_store import heartbeat_store
from core.platform.models import UnifiedContext
from core.primitive_runtime import PrimitiveRuntime
from core.prompt_composer import prompt_composer
from core.skill_loader import skill_loader
from core.task_manager import task_manager
from core.tool_access_store import tool_access_store
from core.tool_broker import ToolBroker
from core.tool_profile_store import tool_profile_store
from core.tool_registry import tool_registry
from services.ai_service import AiService

logger = logging.getLogger(__name__)


class _NoopTodoSession:
    """Compatibility shim to decouple orchestrator from TaskTodoSession."""

    def __init__(self, user_id: str):
        self.todo_path = heartbeat_store.heartbeat_path(user_id)
        self.heartbeat_path = heartbeat_store.status_path(user_id)

    def mark_step(self, *_args, **_kwargs):
        return None

    def heartbeat(self, *_args, **_kwargs):
        return None

    def add_event(self, *_args, **_kwargs):
        return None

    def mark_failed(self, *_args, **_kwargs):
        return None

    def mark_completed(self, *_args, **_kwargs):
        return None


class AgentOrchestrator:
    """Single-loop orchestrator aligned with primitive-first execution."""

    def __init__(self):
        self.ai_service = AiService()
        self.runtime = PrimitiveRuntime()
        self.tool_broker = ToolBroker(self.runtime)
        self.extension_router = ExtensionRouter()
        self.extension_executor = ExtensionExecutor()
        self._memory_tools_cache: List[Dict[str, Any]] | None = None
        self.auto_evolve_enabled = (
            os.getenv("AUTO_EVOLVE_ON_BLOCK", "false").lower() == "true"
        )
        self.always_inject_skill_manager = (
            os.getenv("ALWAYS_INJECT_SKILL_MANAGER", "false").lower() == "true"
        )
        self.direct_fastpath_enabled = (
            os.getenv("DIRECT_EXTENSION_FASTPATH_ENABLED", "false").lower() == "true"
        )
        self.enforce_primitives_first = (
            os.getenv("TOOLS_PRIMITIVE_FIRST_ENFORCED", "true").lower() == "true"
        )
        fastpath_skills = os.getenv(
            "DIRECT_EXTENSION_FASTPATH_SKILLS",
            "",
        )
        self.direct_fastpath_skills = {
            item.strip()
            for item in fastpath_skills.split(",")
            if item.strip()
        }
        logger.info(
            "Orchestrator policy: auto_evolve_enabled=%s, inject_skill_manager=%s, fastpath_enabled=%s, primitives_first=%s, fastpath_skills=%s",
            self.auto_evolve_enabled,
            self.always_inject_skill_manager,
            self.direct_fastpath_enabled,
            self.enforce_primitives_first,
            ",".join(sorted(self.direct_fastpath_skills)) or "(none)",
        )

    async def handle_message(self, ctx: UnifiedContext, message_history: list):
        user_id = ctx.message.user.id
        user_id_str = str(user_id)
        platform_name = str(getattr(ctx.message, "platform", "") or "").strip().lower()
        worker_runtime_user = user_id_str.startswith("worker::")
        heartbeat_runtime_user = platform_name == "heartbeat_daemon"
        # Runtime task state is meaningful only for real user sessions.
        session_state_enabled = not worker_runtime_user and not heartbeat_runtime_user
        runtime_policy_ctx = tool_access_store.resolve_runtime_policy(
            runtime_user_id=user_id_str,
            platform=platform_name,
        )
        last_user_text = self._extract_last_user_text(message_history)
        routing_text = self._extract_recent_user_text(message_history, max_messages=3)
        if not routing_text:
            routing_text = last_user_text
        # Task goal should describe current user message only; routing_text is
        # still used for intent continuity and candidate routing.
        task_goal = last_user_text or routing_text

        task_info = task_manager.get_task_info(user_id)
        task_id = (
            task_info.get("task_id")
            if isinstance(task_info, dict) and task_info.get("task_id")
            else f"{int(datetime.datetime.now().timestamp())}"
        )
        todo_session = _NoopTodoSession(str(user_id))
        workspace_event_logged = False
        session_state_active = False

        async def append_session_event(note: str) -> None:
            if not session_state_enabled:
                return
            await heartbeat_store.append_session_event(str(user_id), note)

        async def update_session_task(
            *,
            status: str | None = None,
            result_summary: str | None = None,
            clear_active: bool = False,
            needs_confirmation: bool | None = None,
            confirmation_deadline: str | None = None,
        ) -> None:
            if not session_state_enabled:
                return
            fields: Dict[str, Any] = {}
            if status is not None:
                fields["status"] = status
            if result_summary is not None:
                fields["result_summary"] = result_summary[:500]
            if needs_confirmation is not None:
                fields["needs_confirmation"] = bool(needs_confirmation)
            if confirmation_deadline is not None:
                fields["confirmation_deadline"] = confirmation_deadline
            if clear_active:
                fields["clear_active"] = True
            if fields:
                await heartbeat_store.update_session_active_task(str(user_id), **fields)

        logger.info(
            "Extension routing text (trimmed): %s",
            routing_text.replace("\n", " | ")[:300],
        )
        raw_extension_candidates = self.extension_router.route(routing_text, max_candidates=3)
        extension_candidates = self._apply_extension_candidate_policy(
            raw_extension_candidates,
            intent_text=routing_text or last_user_text,
        )
        extension_candidates = [
            candidate
            for candidate in extension_candidates
            if self._runtime_tool_allowed(
                runtime_user_id=user_id_str,
                platform=platform_name,
                tool_name=candidate.tool_name,
                kind="tool",
            )
        ]
        extension_candidates = self._rank_extension_candidates(extension_candidates)
        logger.info(
            "Extension candidates selected: raw=%s filtered=%s",
            [candidate.name for candidate in raw_extension_candidates] or "none",
            [candidate.name for candidate in extension_candidates] or "none",
        )
        if extension_candidates:
            candidate_text = ", ".join([candidate.name for candidate in extension_candidates])
            todo_session.mark_step("plan", "done", f"Candidates: {candidate_text}")
        else:
            todo_session.mark_step("plan", "done", "No extension matched; primitives only.")

        task_workspace_root = self._resolve_task_workspace_root(
            extension_candidates=extension_candidates,
            intent_text=routing_text or last_user_text,
        )
        if session_state_enabled:
            await heartbeat_store.set_session_active_task(
                str(user_id),
                {
                    "id": str(task_id),
                    "goal": task_goal,
                    "status": "running",
                    "source": "user_chat",
                    "result_summary": "",
                    "needs_confirmation": False,
                    "confirmation_deadline": "",
                },
            )
            task_manager.set_heartbeat_path(user_id, str(heartbeat_store.heartbeat_path(str(user_id))))
            task_manager.set_active_task_id(user_id, str(task_id))
            task_manager.heartbeat(user_id, f"session:{task_id}:running")
            await append_session_event(f"session_started:{task_id}")
            session_state_active = True
            if task_workspace_root and not workspace_event_logged:
                await append_session_event(f"workspace_root:{task_workspace_root}")
                workspace_event_logged = True

        direct_response = await self._try_direct_extension_execution(
            ctx=ctx,
            user_text=last_user_text,
            intent_context=routing_text,
            extension_candidates=extension_candidates,
            todo_session=todo_session,
        )
        if direct_response is not None:
            await update_session_task(
                status="done",
                result_summary=str(direct_response),
                clear_active=True,
            )
            await append_session_event(f"direct_extension_done:{task_id}")
            task_manager.heartbeat(user_id, "direct_extension_done")
            yield direct_response
            return

        extension_map = {candidate.tool_name: candidate for candidate in extension_candidates}
        primitive_calls_completed = 0

        tools: List[Dict[str, Any]] = []
        for item in tool_registry.get_core_tools():
            name = str(item.get("name") or "")
            if self._runtime_tool_allowed(
                runtime_user_id=user_id_str,
                platform=platform_name,
                tool_name=name,
                kind="tool",
            ):
                tools.append(item)
        for item in tool_registry.get_extension_tools(extension_candidates):
            name = str(item.get("name") or "")
            if self._runtime_tool_allowed(
                runtime_user_id=user_id_str,
                platform=platform_name,
                tool_name=name,
                kind="tool",
            ):
                tools.append(item)

        if MCP_MEMORY_ENABLED:
            memory_tools = await self._get_memory_tool_definitions(user_id)
            if memory_tools:
                for item in memory_tools:
                    name = ""
                    if isinstance(item, dict):
                        name = str(item.get("name") or "")
                    elif hasattr(item, "name"):
                        name = str(item.name or "")
                    if not name:
                        continue
                    if self._runtime_tool_allowed(
                        runtime_user_id=user_id_str,
                        platform=platform_name,
                        tool_name=name,
                        kind="mcp",
                    ):
                        tools.append(item)

        async def tool_executor(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal primitive_calls_completed
            logger.info("Agent invoking tool: %s with args=%s", name, args)
            await append_session_event(f"tool_start:{task_id}:{name}")
            todo_session.mark_step("act", "in_progress", f"Calling tool `{name}`")
            todo_session.heartbeat(f"tool:{name}:start")
            task_manager.heartbeat(user_id, f"tool:{name}:start")
            execution_policy = self.tool_broker.resolve_policy(ctx)
            started = time.perf_counter()

            try:
                if name in {"read", "write", "edit", "bash"}:
                    if not self._runtime_tool_allowed(
                        runtime_user_id=user_id_str,
                        platform=platform_name,
                        tool_name=name,
                        kind="tool",
                    ):
                        blocked = {
                            "ok": False,
                            "error_code": "policy_blocked",
                            "message": f"Tool policy blocked: {name}",
                            "failure_mode": "recoverable",
                        }
                        self._record_tool_profile(name=name, result=blocked, started=started)
                        return blocked
                    result = await self.tool_broker.execute_core_tool(
                        name=name,
                        args=args,
                        execution_policy=execution_policy,
                        task_workspace_root=task_workspace_root,
                    )
                    primitive_calls_completed += 1
                    todo_session.mark_step("act", "in_progress", f"Tool `{name}` finished.")
                    self._record_tool_profile(name=name, result=result, started=started)
                    return result

                if name in extension_map:
                    candidate = extension_map[name]
                    if not self._runtime_tool_allowed(
                        runtime_user_id=user_id_str,
                        platform=platform_name,
                        tool_name=name,
                        kind="tool",
                    ):
                        blocked = {
                            "ok": False,
                            "error_code": "policy_blocked",
                            "message": f"Tool policy blocked: {name}",
                            "failure_mode": "recoverable",
                        }
                        self._record_tool_profile(name=name, result=blocked, started=started)
                        return blocked
                    if (
                        self.enforce_primitives_first
                        and primitive_calls_completed < 1
                        and not self._extension_explicitly_requested(
                            candidate=candidate,
                            intent_text=routing_text or last_user_text,
                            latest_user_text=last_user_text,
                        )
                    ):
                        blocked_msg = (
                            "Policy: 先执行至少一个核心原语（read/write/edit/bash）再调用扩展工具。"
                        )
                        await append_session_event(
                            f"tool_finish:{task_id}:{name}:blocked:primitive_first"
                        )
                        self._record_tool_profile(
                            name=name,
                            result={"ok": False, "message": blocked_msg},
                            started=started,
                        )
                        return {
                            "ok": False,
                            "error_code": "primitive_first_required",
                            "message": blocked_msg,
                            "failure_mode": "recoverable",
                        }
                    await ctx.reply(f"⚡ 正在执行扩展 `{candidate.name}`...")
                    result = await self.extension_executor.execute(
                        skill_name=candidate.name,
                        args=args,
                        ctx=ctx,
                        runtime=self.runtime,
                    )

                    if result.files:
                        for filename, content in result.files.items():
                            await ctx.reply_document(document=content, filename=filename)

                    if result.ok:
                        todo_session.mark_step(
                            "act",
                            "in_progress",
                            f"Extension `{candidate.name}` finished.",
                        )
                    else:
                        todo_session.mark_step(
                            "act",
                            "blocked",
                            f"Extension `{candidate.name}` failed: {result.message or result.error_code}",
                        )
                    await append_session_event(
                        f"tool_finish:{task_id}:{name}:{'ok' if result.ok else 'failed'}"
                    )
                    tool_result = result.to_tool_response()
                    self._record_tool_profile(name=name, result=tool_result, started=started)
                    return tool_result

                if self._is_memory_tool(name):
                    if not self._runtime_tool_allowed(
                        runtime_user_id=user_id_str,
                        platform=platform_name,
                        tool_name=name,
                        kind="mcp",
                    ):
                        blocked = {
                            "ok": False,
                            "error_code": "policy_blocked",
                            "message": f"Tool policy blocked: {name}",
                            "failure_mode": "recoverable",
                        }
                        self._record_tool_profile(name=name, result=blocked, started=started)
                        return blocked
                    memory_server = await self._get_active_memory_server(user_id)
                    if memory_server:
                        result = await memory_server.call_tool(name, args)
                        todo_session.mark_step(
                            "act",
                            "in_progress",
                            f"Memory tool `{name}` finished.",
                        )
                        self._record_tool_profile(name=name, result={"ok": True}, started=started)
                        return result

                todo_session.mark_step("act", "blocked", f"Unknown tool `{name}`.")
                await append_session_event(f"tool_finish:{task_id}:{name}:unknown_tool")
                unknown = {
                    "ok": False,
                    "error_code": "unknown_tool",
                    "message": f"Unknown tool: {name}",
                }
                self._record_tool_profile(name=name, result=unknown, started=started)
                return unknown

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Error in tool_executor: %s", exc, exc_info=True)
                todo_session.mark_step("act", "blocked", f"Tool `{name}` exception: {exc}")
                await append_session_event(f"tool_finish:{task_id}:{name}:exception:{exc}")
                error_result = {
                    "ok": False,
                    "error_code": "system_error",
                    "message": str(exc),
                }
                self._record_tool_profile(name=name, result=error_result, started=started)
                return error_result

        system_instruction = self._build_system_instruction(
            extension_candidates,
            intent_text=routing_text or last_user_text,
            runtime_user_id=user_id_str,
            runtime_policy_ctx=runtime_policy_ctx,
            tools=tools,
        )
        tools = self._filter_model_tools_by_policy(
            tools=tools,
            runtime_user_id=user_id_str,
            platform=platform_name,
        )

        todo_flags = {"blocked": False, "completed": False, "blocked_reason": ""}
        suppressed_max_turn_warning = ""
        max_recovery_attempts = max(1, int(AUTO_RECOVERY_MAX_ATTEMPTS))
        recovery_attempts = 0

        async def on_agent_event(event: str, payload: Dict[str, Any]):
            if event == "turn_start":
                turn = payload.get("turn")
                if session_state_active:
                    await heartbeat_store.pulse(str(user_id), f"turn:{turn}")
                todo_session.heartbeat(f"turn:{turn}")
                task_manager.heartbeat(user_id, f"turn:{turn}")
                return

            if event == "tool_call_started":
                tool_name = payload.get("name", "unknown")
                await append_session_event(f"tool_call_started:{task_id}:{tool_name}")
                todo_session.mark_step("act", "in_progress", f"Running `{tool_name}`...")
                task_manager.heartbeat(user_id, f"tool:{tool_name}:running")
                return

            if event == "tool_call_finished":
                nonlocal recovery_attempts
                tool_name = payload.get("name", "unknown")
                ok = bool(payload.get("ok"))
                summary = str(payload.get("summary", "")).strip()
                terminal = bool(payload.get("terminal"))
                task_outcome = str(payload.get("task_outcome") or "").strip().lower()
                terminal_text = str(payload.get("terminal_text") or "").strip()
                failure_mode = (
                    str(payload.get("failure_mode") or "").strip().lower() or "recoverable"
                )
                if failure_mode not in {"recoverable", "fatal"}:
                    failure_mode = "recoverable"
                if ok:
                    todo_session.mark_step(
                        "act",
                        "in_progress",
                        f"`{tool_name}` ok: {summary[:120]}",
                    )
                else:
                    todo_session.mark_step(
                        "act",
                        "blocked",
                        f"`{tool_name}` failed: {summary[:180]}",
                    )
                    todo_session.mark_step(
                        "verify",
                        "in_progress",
                        "Detected failure; waiting for automatic retry.",
                    )
                    if failure_mode == "recoverable" and recovery_attempts < max_recovery_attempts:
                        recovery_attempts += 1
                await append_session_event(
                    (
                        f"tool_call_finished:{task_id}:{tool_name}:"
                        f"{'ok' if ok else 'failed'}:{failure_mode}:{summary[:160]}"
                    )
                )
                task_manager.heartbeat(user_id, f"tool:{tool_name}:{'ok' if ok else 'failed'}")
                if terminal:
                    final_text = terminal_text or summary or (
                        "✅ 工具执行完成。" if ok else f"❌ `{tool_name}` 执行失败。"
                    )
                    if ok and task_outcome == "partial":
                        deadline = (
                            datetime.datetime.now().astimezone()
                            + datetime.timedelta(seconds=180)
                        ).isoformat(timespec="seconds")
                        await update_session_task(
                            status="waiting_user",
                            result_summary=final_text,
                            needs_confirmation=True,
                            confirmation_deadline=deadline,
                        )
                        await append_session_event(f"task_waiting_user:{task_id}:{tool_name}")
                        ctx.user_data["pending_ui"] = [
                            {
                                "actions": [
                                    [
                                        {
                                            "text": "继续执行",
                                            "callback_data": "task_continue",
                                        },
                                        {
                                            "text": "停止任务",
                                            "callback_data": "task_stop",
                                        },
                                    ]
                                ]
                            }
                        ]
                        final_text = (
                            f"{final_text}\n\n"
                            "请确认下一步：点击按钮，或直接回复“继续”/“停止”（3分钟内有效）。"
                        )
                    elif ok:
                        await update_session_task(
                            status="done",
                            result_summary=final_text,
                            needs_confirmation=False,
                            confirmation_deadline="",
                            clear_active=True,
                        )
                        await append_session_event(f"terminal_tool_done:{task_id}:{tool_name}")
                    else:
                        if failure_mode == "recoverable" and recovery_attempts < max_recovery_attempts:
                            await append_session_event(
                                (
                                    f"recoverable_terminal_failure:{task_id}:{tool_name}:"
                                    f"attempt={recovery_attempts}/{max_recovery_attempts}"
                                )
                            )
                            todo_session.mark_step(
                                "verify",
                                "in_progress",
                                (
                                    f"Recoverable failure detected. "
                                    f"Auto-recovery attempt {recovery_attempts}/{max_recovery_attempts}."
                                ),
                            )
                            await update_session_task(
                                status="running",
                                result_summary=summary,
                                needs_confirmation=False,
                                confirmation_deadline="",
                            )
                            return

                        await update_session_task(
                            status="failed",
                            result_summary=final_text,
                            needs_confirmation=False,
                            confirmation_deadline="",
                            clear_active=True,
                        )
                        await append_session_event(f"terminal_tool_failed:{task_id}:{tool_name}")
                    todo_flags["completed"] = True
                    return {"stop": True, "final_text": final_text}
                return

            if event == "retry_after_failure":
                failures = payload.get("failures")
                failure_list = failures if isinstance(failures, list) else []
                stage = max(1, min(max_recovery_attempts, recovery_attempts or 1))
                await append_session_event(
                    f"retry_after_failure:{task_id}:attempt={stage}/{max_recovery_attempts}"
                )
                todo_session.mark_step(
                    "act",
                    "in_progress",
                    "Retrying after tool failure.",
                )
                task_manager.heartbeat(user_id, "retry_after_failure")
                return {
                    "recovery_instruction": self._build_recovery_instruction(
                        stage=stage,
                        max_attempts=max_recovery_attempts,
                        failures=failure_list,
                    )
                }

            if event == "final_response":
                todo_session.mark_step("verify", "done", "Model produced final response.")
                todo_session.mark_step("deliver", "done", "Final response streaming.")
                if session_state_active:
                    preview = str(payload.get("text_preview", "")).strip()
                    current = await heartbeat_store.get_session_active_task(str(user_id))
                    current_status = str((current or {}).get("status", "")).strip().lower()
                    if current_status not in {"waiting_user", "failed", "cancelled", "timed_out"}:
                        await update_session_task(
                            status="done",
                            result_summary=preview,
                            needs_confirmation=False,
                            confirmation_deadline="",
                            clear_active=True,
                        )
                    await append_session_event(f"final_response:{task_id}:{preview[:120]}")
                task_manager.heartbeat(user_id, "final_response")
                todo_flags["completed"] = True
                return

            if event == "max_turn_limit":
                terminal_preview = str(payload.get("terminal_text_preview") or "").strip()
                terminal_summary = str(payload.get("terminal_summary") or "").strip()
                if session_state_active and (terminal_preview or terminal_summary):
                    summary = (terminal_preview or terminal_summary)[:500]
                    await update_session_task(
                        status="done",
                        result_summary=summary,
                        needs_confirmation=False,
                        confirmation_deadline="",
                        clear_active=True,
                    )
                    await append_session_event(f"max_turn_but_completed:{task_id}:{summary[:120]}")
                    todo_flags["completed"] = True
                    return
                todo_session.mark_failed("Reached max tool-loop turns before completion.")
                if session_state_active:
                    await update_session_task(
                        status="failed",
                        result_summary="Reached max tool-loop turns before completion.",
                        needs_confirmation=False,
                        confirmation_deadline="",
                        clear_active=True,
                    )
                    await append_session_event(f"max_turn_limit:{task_id}")
                task_manager.heartbeat(user_id, "max_turn_limit")
                todo_flags["blocked"] = True
                todo_flags["blocked_reason"] = "max_turn_limit"
                return

            if event == "loop_guard":
                if session_state_active:
                    await update_session_task(
                        status="failed",
                        result_summary="Loop guard triggered due to repeated tool calls.",
                        needs_confirmation=False,
                        confirmation_deadline="",
                        clear_active=True,
                    )
                    await append_session_event(f"loop_guard:{task_id}:{payload.get('repeat_count')}")
                todo_flags["blocked"] = True
                todo_flags["blocked_reason"] = "loop_guard"
                return

        async for chunk in self.ai_service.generate_response_stream(
            message_history,
            tools=tools,
            tool_executor=tool_executor,
            system_instruction=system_instruction,
            event_callback=on_agent_event,
        ):
            task_manager.heartbeat(user_id, "streaming")
            if isinstance(chunk, str) and "工具调用轮次已达上限" in chunk:
                suppressed_max_turn_warning = chunk
                continue
            yield chunk

        if (
            todo_flags["blocked"]
            and todo_flags.get("blocked_reason") == "max_turn_limit"
            and self._should_auto_evolve(
                intent_text=routing_text or last_user_text,
                extension_candidates=extension_candidates,
            )
        ):
            todo_session.mark_step(
                "act",
                "in_progress",
                "Primary tools insufficient; attempting automatic skill evolution.",
            )
            evolve_ok, evolve_msg = await self._attempt_auto_skill_evolution(
                ctx=ctx,
                user_request=routing_text or last_user_text,
                todo_session=todo_session,
            )

            if evolve_msg:
                yield evolve_msg

            if evolve_ok:
                # Re-route after evolution and run one more loop automatically.
                reroute_candidates = self.extension_router.route(
                    routing_text, max_candidates=3
                )
                extension_candidates = self._apply_extension_candidate_policy(
                    reroute_candidates,
                    intent_text=routing_text or last_user_text,
                )
                extension_candidates = self._rank_extension_candidates(extension_candidates)
                extension_map = {
                    candidate.tool_name: candidate for candidate in extension_candidates
                }

                tools = []
                for item in tool_registry.get_core_tools():
                    name = str(item.get("name") or "")
                    if self._runtime_tool_allowed(
                        runtime_user_id=user_id_str,
                        platform=platform_name,
                        tool_name=name,
                        kind="tool",
                    ):
                        tools.append(item)
                for item in tool_registry.get_extension_tools(extension_candidates):
                    name = str(item.get("name") or "")
                    if self._runtime_tool_allowed(
                        runtime_user_id=user_id_str,
                        platform=platform_name,
                        tool_name=name,
                        kind="tool",
                    ):
                        tools.append(item)

                if MCP_MEMORY_ENABLED:
                    memory_tools = await self._get_memory_tool_definitions(user_id)
                    if memory_tools:
                        for item in memory_tools:
                            name = ""
                            if isinstance(item, dict):
                                name = str(item.get("name") or "")
                            elif hasattr(item, "name"):
                                name = str(item.name or "")
                            if not name:
                                continue
                            if self._runtime_tool_allowed(
                                runtime_user_id=user_id_str,
                                platform=platform_name,
                                tool_name=name,
                                kind="mcp",
                            ):
                                tools.append(item)

                system_instruction = self._build_system_instruction(
                    extension_candidates,
                    intent_text=routing_text or last_user_text,
                    runtime_user_id=user_id_str,
                    runtime_policy_ctx=runtime_policy_ctx,
                    tools=tools,
                )
                tools = self._filter_model_tools_by_policy(
                    tools=tools,
                    runtime_user_id=user_id_str,
                    platform=platform_name,
                )

                todo_flags["blocked"] = False
                todo_flags["completed"] = False
                todo_flags["blocked_reason"] = ""
                suppressed_max_turn_warning = ""

                async for chunk in self.ai_service.generate_response_stream(
                    message_history,
                    tools=tools,
                    tool_executor=tool_executor,
                    system_instruction=system_instruction,
                    event_callback=on_agent_event,
                ):
                    task_manager.heartbeat(user_id, "streaming_after_evolution")
                    if isinstance(chunk, str) and "工具调用轮次已达上限" in chunk:
                        suppressed_max_turn_warning = chunk
                        continue
                    yield chunk

            if todo_flags["blocked"] and suppressed_max_turn_warning:
                yield suppressed_max_turn_warning
        elif todo_flags["blocked"] and suppressed_max_turn_warning:
            yield suppressed_max_turn_warning

        if not todo_flags["blocked"] and not todo_flags["completed"]:
            todo_session.mark_completed("Conversation loop completed.")
            if session_state_active:
                await update_session_task(
                    status="done",
                    result_summary="Conversation loop completed.",
                    needs_confirmation=False,
                    confirmation_deadline="",
                    clear_active=True,
                )
                await append_session_event(f"conversation_completed:{task_id}")

    @staticmethod
    def _build_recovery_instruction(
        stage: int,
        max_attempts: int,
        failures: list[str] | None = None,
    ) -> str:
        failure_text = "; ".join([str(item) for item in (failures or [])[:3]])
        failure_text = failure_text or "unknown"
        normalized_stage = max(1, min(max_attempts, int(stage)))

        if normalized_stage == 1:
            strategy = (
                "优先在同一工具/扩展内自修复并立即重试，"
                "例如补齐缺失参数、修复输入格式、处理可恢复环境错误。"
            )
        elif normalized_stage == 2:
            strategy = (
                "不要继续卡在当前扩展；切换到四原语 `read/write/edit/bash` 进行排障与修复，"
                "再回到目标执行。"
            )
        else:
            strategy = (
                "尝试备选扩展或备选方案，给出可交付结果；"
                "若仍失败，输出清晰失败报告并列出已尝试步骤。"
            )

        return (
            "系统提示：上一步工具执行失败，任务尚未完成。"
            f"恢复阶段 {normalized_stage}/{max_attempts}：{strategy} "
            "除非确实缺少关键必填信息，否则不要先向用户提问。"
            f"失败摘要：{failure_text}"
        )

    def _build_system_instruction(
        self,
        extension_candidates: list,
        intent_text: str = "",
        runtime_user_id: str = "",
        runtime_policy_ctx: Dict[str, Any] | None = None,
        tools: List[Dict[str, Any]] | None = None,
    ) -> str:
        del extension_candidates
        del intent_text
        return prompt_composer.compose_base(
            runtime_user_id=runtime_user_id,
            tools=tools or [],
            runtime_policy_ctx=runtime_policy_ctx or {},
            mode="chat",
        )

    def _build_runtime_facts(self) -> str:
        in_docker = os.path.exists("/.dockerenv") or (
            os.getenv("RUNNING_IN_DOCKER", "").lower() == "true"
        )
        docker_socket = os.path.exists("/var/run/docker.sock")

        facts = [
            f"- Runtime: {'Docker container' if in_docker else 'Host process'}",
            f"- Workspace root: `{self.runtime.workspace_root}`",
            f"- DATA_DIR: `{DATA_DIR}`",
            "- Use workspace-relative paths unless tool/doc explicitly requires absolute host paths.",
        ]
        if docker_socket:
            facts.append(
                "- Docker socket `/var/run/docker.sock` 已挂载，可直接执行 `docker`/`docker compose`。"
            )
        else:
            facts.append(
                "- 未检测到 Docker socket，若 `docker` 命令失败需提示用户检查挂载与权限。"
            )
        if X_DEPLOYMENT_STAGING_PATH:
            facts.append(f"- X_DEPLOYMENT_STAGING_PATH: `{X_DEPLOYMENT_STAGING_PATH}`")
            facts.append(
                "- 对于部署/Compose任务，优先在该绝对路径下执行文件与命令，保持容器内外路径一致。"
            )
        if SERVER_IP:
            facts.append(f"- SERVER_IP: `{SERVER_IP}`")
            facts.append("- 对外访问地址优先使用该主机/IP，不要使用占位符。")
        return "\n".join(facts)

    def _resolve_task_workspace_root(
        self,
        extension_candidates: list,
        intent_text: str = "",
    ) -> str:
        staging_path = (X_DEPLOYMENT_STAGING_PATH or "").strip()
        if not staging_path:
            return ""

        candidate_names = {
            getattr(candidate, "name", "") for candidate in (extension_candidates or [])
        }
        if "deployment_manager" not in candidate_names and not self._is_deployment_intent(
            intent_text
        ):
            return ""

        resolved = os.path.abspath(os.path.expanduser(staging_path))
        try:
            os.makedirs(resolved, exist_ok=True)
        except Exception:
            pass
        return resolved

    def _normalize_task_path(self, path: str, task_workspace_root: str = "") -> str:
        raw = str(path or "").strip()
        if not raw or not task_workspace_root:
            return raw
        expanded = os.path.expanduser(raw)
        if os.path.isabs(expanded):
            return expanded
        return os.path.abspath(os.path.join(task_workspace_root, expanded))

    def _normalize_task_cwd(self, cwd: str | None, task_workspace_root: str = "") -> str | None:
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

    def _is_deployment_intent(self, text: str) -> bool:
        lowered = (text or "").lower()
        if not lowered.strip():
            return False
        keywords = (
            "部署",
            "deploy",
            "docker compose",
            "compose",
            "k8s",
            "上线",
            "发布",
            "install service",
        )
        return any(keyword in lowered for keyword in keywords)

    def _build_candidate_skill_briefs(
        self,
        extension_candidates: list,
        max_skills: int = 3,
    ) -> str:
        if not extension_candidates:
            return ""

        lines: List[str] = []
        for candidate in extension_candidates[:max_skills]:
            skill = skill_loader.get_skill(candidate.name)
            if not skill:
                continue

            skill_path = skill.get("skill_md_path") or f"skills/*/{candidate.name}/SKILL.md"
            content = str(skill.get("skill_md_content") or "")
            items = self._extract_skill_guideline_items(content)
            if not items:
                continue

            lines.append(f"- `{candidate.name}` ({skill_path})")
            for item in items[:3]:
                lines.append(f"  - {item}")

        return "\n".join(lines)

    def _extract_skill_guideline_items(self, content: str) -> List[str]:
        if not content.strip():
            return []

        keywords = (
            "核心",
            "原则",
            "注意",
            "安全",
            "步骤",
            "验证",
            "参数",
            "限制",
            "must",
            "should",
            "required",
        )
        picked: List[str] = []
        for raw in content.splitlines():
            line = raw.strip()
            if not line:
                continue
            normalized = re.sub(r"^[#\-\*\d\.\)\s:：]+", "", line).strip()
            if len(normalized) < 6:
                continue
            lowered = normalized.lower()
            if any(kw in lowered for kw in keywords):
                if normalized not in picked:
                    picked.append(normalized[:180])
            if len(picked) >= 6:
                break
        return picked

    def _apply_extension_candidate_policy(
        self,
        extension_candidates: list,
        intent_text: str = "",
    ) -> list:
        deduped: List[ExtensionCandidate] = []
        seen_names: set[str] = set()
        for candidate in extension_candidates or []:
            name = getattr(candidate, "name", "")
            if not name or name in seen_names:
                continue
            deduped.append(candidate)
            seen_names.add(name)

        has_non_skill_candidate = any(
            getattr(candidate, "name", "") != "skill_manager"
            for candidate in deduped
        )
        should_inject_skill_manager = False
        if self._is_skill_management_intent(intent_text):
            should_inject_skill_manager = True
        elif (
            self.always_inject_skill_manager
            and not has_non_skill_candidate
            and self._is_task_or_goal_intent(intent_text)
        ):
            should_inject_skill_manager = True

        if should_inject_skill_manager:
            skill_manager_candidate = self._build_skill_manager_candidate()
            if skill_manager_candidate and skill_manager_candidate.name not in seen_names:
                deduped.append(skill_manager_candidate)

        return deduped

    def _rank_extension_candidates(
        self,
        extension_candidates: list,
    ) -> list:
        if not extension_candidates:
            return []
        ranked = sorted(
            extension_candidates,
            key=lambda candidate: tool_profile_store.score_tool(candidate.tool_name),
            reverse=True,
        )
        return ranked

    def _filter_model_tools_by_policy(
        self,
        *,
        tools: List[Any],
        runtime_user_id: str,
        platform: str,
    ) -> List[Any]:
        """Final hard gate before function-call tools are injected into model config."""
        filtered: List[Any] = []
        seen: set[str] = set()
        for item in tools or []:
            name = ""
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
            else:
                name = str(getattr(item, "name", "") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            if self._runtime_tool_allowed(
                runtime_user_id=runtime_user_id,
                platform=platform,
                tool_name=name,
                kind="tool",
            ):
                filtered.append(item)
        if len(filtered) != len(seen):
            logger.info(
                "Function-call tools filtered by policy: user=%s platform=%s before=%s after=%s",
                runtime_user_id,
                platform,
                len(seen),
                len(filtered),
            )
        return filtered

    def _runtime_tool_allowed(
        self,
        *,
        runtime_user_id: str,
        platform: str,
        tool_name: str,
        kind: str = "tool",
    ) -> bool:
        allowed, detail = tool_access_store.is_tool_allowed(
            runtime_user_id=runtime_user_id,
            platform=platform,
            tool_name=tool_name,
            kind=kind,
        )
        if not allowed:
            logger.info(
                "Tool blocked by policy: user=%s tool=%s kind=%s groups=%s reason=%s agent=%s:%s",
                runtime_user_id,
                tool_name,
                kind,
                ",".join(detail.get("groups") or []),
                detail.get("reason"),
                detail.get("agent_kind"),
                detail.get("agent_id"),
            )
        return allowed

    def _record_tool_profile(self, name: str, result: Any, started: float) -> None:
        elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        success = True
        if isinstance(result, dict):
            if "ok" in result:
                success = bool(result.get("ok"))
            elif result.get("success") is False:
                success = False
            else:
                text = str(result.get("message") or result.get("summary") or "")
                success = not text.lower().startswith(("error", "❌"))
        elif isinstance(result, str):
            success = not str(result).strip().lower().startswith(("error", "❌"))
        try:
            tool_profile_store.record(name, success=success, latency_ms=elapsed_ms)
        except Exception:
            logger.debug("Failed to record tool profile: %s", name, exc_info=True)

    def _extension_explicitly_requested(
        self,
        *,
        candidate: ExtensionCandidate,
        intent_text: str,
        latest_user_text: str,
    ) -> bool:
        merged = f"{intent_text}\n{latest_user_text}".lower()
        name = str(candidate.name or "").lower()
        tool_name = str(candidate.tool_name or "").lower()
        if name and name in merged:
            return True
        if tool_name and tool_name in merged:
            return True
        if f"/{name}" in merged:
            return True
        for trig in candidate.triggers or []:
            token = str(trig or "").strip().lower()
            if token and token in merged:
                return True
        return False

    def _should_auto_evolve(
        self,
        intent_text: str,
        extension_candidates: list,
    ) -> bool:
        if not self.auto_evolve_enabled:
            return False
        if self._is_skill_management_intent(intent_text):
            return True
        if self._has_evolution_confirmation(intent_text):
            return True
        return False

    def _build_skill_manager_candidate(self) -> ExtensionCandidate | None:
        skill = skill_loader.get_skill("skill_manager")
        if not skill:
            return None
        schema = skill.get("input_schema") or {"type": "object", "properties": {}}
        props = list((schema.get("properties") or {}).keys())
        required = list(schema.get("required") or [])
        return ExtensionCandidate(
            name="skill_manager",
            description=skill.get("description", "Skill management and evolution"),
            tool_name="ext_skill_manager",
            input_schema=schema,
            schema_summary=f"required={required}, fields={props[:8]}",
            triggers=skill.get("triggers", []) or [],
        )

    async def _attempt_auto_skill_evolution(
        self,
        ctx: UnifiedContext,
        user_request: str,
        todo_session: Any | None = None,
    ) -> tuple[bool, str]:
        if not user_request.strip():
            return False, ""

        result = await self.extension_executor.execute(
            skill_name="skill_manager",
            args={"action": "create", "requirement": user_request},
            ctx=ctx,
            runtime=self.runtime,
        )
        if result.ok:
            if todo_session:
                todo_session.heartbeat("auto_evolution:ok")
                todo_session.mark_step(
                    "act",
                    "in_progress",
                    "Automatic skill evolution succeeded; rerunning task.",
                )
            return True, self._sanitize_skill_text(result.text or "🛠️ 自动技能进化完成。")

        message = result.message or result.error_code or "unknown_error"
        if todo_session:
            todo_session.heartbeat(f"auto_evolution:failed:{message}")
            todo_session.mark_step(
                "act",
                "blocked",
                f"Automatic skill evolution failed: {message}",
            )
        return False, f"⚠️ 自动技能进化失败：{message}"

    def _is_task_or_goal_intent(self, text: str) -> bool:
        lowered = (text or "").lower().strip()
        if not lowered:
            return False
        if len(lowered) >= 20:
            return True

        keywords = (
            "帮我",
            "请",
            "完成",
            "实现",
            "部署",
            "下载",
            "安装",
            "创建",
            "修复",
            "写",
            "build",
            "deploy",
            "install",
            "fix",
            "create",
            "implement",
        )
        return any(keyword in lowered for keyword in keywords)

    def _has_evolution_confirmation(self, text: str) -> bool:
        lowered = (text or "").lower()
        cues = (
            "允许创建技能",
            "同意创建技能",
            "确认创建技能",
            "继续进化",
            "allow evolve",
            "allow skill creation",
            "create new skill",
        )
        return any(cue in lowered for cue in cues)

    def _is_skill_management_intent(self, text: str) -> bool:
        lowered = (text or "").lower()
        if not lowered.strip():
            return False

        keywords = (
            "skill_manager",
            "技能",
            "skill",
            "teach",
            "教我",
            "创建技能",
            "新技能",
            "learned skill",
            "删除技能",
            "修改技能",
            "重载技能",
        )
        return any(keyword in lowered for keyword in keywords)

    async def _try_direct_extension_execution(
        self,
        ctx: UnifiedContext,
        user_text: str,
        intent_context: str,
        extension_candidates: list,
        todo_session: Any | None = None,
    ) -> str | None:
        """
        Generic fast-path for explicit intents that should execute immediately.
        """
        if not self.direct_fastpath_enabled:
            return None

        text = (user_text or "").strip()
        if not text:
            return None
        merged_intent_text = (intent_context or text).strip()
        selected_names = self.direct_fastpath_skills or {
            candidate.name for candidate in extension_candidates or []
        }

        for candidate in extension_candidates or []:
            if candidate.name not in selected_names:
                continue
            if not self._is_direct_fastpath_intent(candidate, text, merged_intent_text):
                continue

            args = self._build_direct_extension_args(
                candidate=candidate,
                text=text or merged_intent_text,
                merged_intent_text=merged_intent_text,
            )
            if not args:
                continue

            start_message = f"⚡ 已识别为 `{candidate.name}` 任务，正在直接执行..."
            success_fallback = f"✅ `{candidate.name}` 执行完成。"
            failure_prefix = f"`{candidate.name}` 执行失败"

            response = await self._execute_direct_extension(
                ctx=ctx,
                candidate=candidate,
                args=args,
                start_message=start_message,
                success_fallback=success_fallback,
                failure_prefix=failure_prefix,
                todo_session=todo_session,
            )
            if response is not None:
                return response

        return None

    def _is_direct_fastpath_intent(
        self,
        candidate: ExtensionCandidate,
        text: str,
        merged_intent_text: str,
    ) -> bool:
        lowered = text.lower()
        merged_lowered = merged_intent_text.lower()
        trigger_tokens = [str(candidate.name).lower()]
        trigger_tokens.extend(
            str(item).lower()
            for item in getattr(candidate, "triggers", []) or []
            if str(item).strip()
        )
        description_tokens = re.findall(
            r"[a-zA-Z0-9_\-\u4e00-\u9fff]{2,}",
            str(getattr(candidate, "description", "") or "").lower(),
        )
        trigger_tokens.extend(description_tokens[:12])
        trigger_tokens = [token for token in trigger_tokens if len(token) >= 2]
        if any(token in lowered for token in trigger_tokens):
            return True
        if self._is_followup_detail_reply(text) and any(
            token in merged_lowered for token in trigger_tokens
        ):
            return True
        if self._is_task_or_goal_intent(text) and any(
            token in merged_lowered for token in trigger_tokens
        ):
            return True
        return False

    def _build_direct_extension_args(
        self,
        candidate: ExtensionCandidate,
        text: str,
        merged_intent_text: str = "",
    ) -> Dict[str, Any]:
        schema = candidate.input_schema or {"type": "object", "properties": {}}
        props = schema.get("properties") or {}
        args: Dict[str, Any] = {}
        payload_text = (text or merged_intent_text or "").strip()
        for key, prop in props.items():
            if isinstance(prop, dict) and "default" in prop:
                args[key] = prop["default"]

        # Schema-driven action selection for extensions that require action field.
        if "action" in props and "action" not in args:
            action_prop = props.get("action") or {}
            enum_values = action_prop.get("enum") if isinstance(action_prop, dict) else []
            if isinstance(enum_values, list) and enum_values:
                if "auto_deploy" in enum_values and (
                    "request" in props or "service" in props
                ):
                    args["action"] = "auto_deploy"
                else:
                    args["action"] = str(enum_values[0])
            else:
                semantic = " ".join(
                    [
                        str(candidate.name or "").lower(),
                        str(candidate.description or "").lower(),
                        " ".join(str(item).lower() for item in (candidate.triggers or [])),
                        payload_text.lower(),
                    ]
                )
                if (
                    ("deploy" in semantic or "部署" in semantic)
                    and ("request" in props or "service" in props or "repo_url" in props)
                ):
                    args["action"] = "auto_deploy"

        url_match = re.search(r"https?://[^\s)]+", payload_text)
        if url_match and "repo_url" in props:
            args["repo_url"] = url_match.group(0).rstrip(".,);")

        port_match = re.search(
            r"(?:端口|port)\s*[:：]?\s*(\d{2,5})",
            payload_text,
            flags=re.IGNORECASE,
        )
        if port_match and "host_port" in props:
            try:
                args["host_port"] = int(port_match.group(1))
            except ValueError:
                pass

        if "service" in props:
            service_name = self._extract_deployment_service(payload_text)
            if service_name:
                args["service"] = service_name

        if "topic" in props:
            args["topic"] = payload_text

        depth_match = re.search(
            r"(?:深度|depth)\s*[:：]?\s*(\d{1,2})",
            payload_text,
            flags=re.IGNORECASE,
        )
        if depth_match and "depth" in props:
            try:
                args["depth"] = int(depth_match.group(1))
            except ValueError:
                pass

        lang_match = re.search(
            r"(?:语言|language)\s*[:：]?\s*([a-z]{2}(?:-[a-z]{2})?)",
            payload_text,
            flags=re.IGNORECASE,
        )
        if lang_match and "language" in props:
            args["language"] = lang_match.group(1)

        for field in ("request", "query", "instruction", "topic", "text"):
            if field in props and field not in args:
                args[field] = payload_text
                break

        required = schema.get("required") or []
        for field in required:
            if field not in args or args[field] in ("", None):
                return {}
        return args

    def _find_extension_candidate(self, extension_candidates: list, name: str):
        for candidate in extension_candidates:
            if candidate.name == name:
                return candidate
        return None

    async def _execute_direct_extension(
        self,
        ctx: UnifiedContext,
        candidate,
        args: Dict[str, Any],
        start_message: str,
        success_fallback: str,
        failure_prefix: str,
        todo_session: Any | None = None,
    ) -> str | None:
        logger.info(
            "Direct extension execution triggered: %s args=%s",
            candidate.name,
            args,
        )
        started = time.perf_counter()
        if todo_session:
            todo_session.mark_step(
                "act", "in_progress", f"Directly executing extension `{candidate.name}`."
            )
            todo_session.heartbeat(f"direct_extension:{candidate.name}:start")
        await ctx.reply(start_message)

        result = await self.extension_executor.execute(
            skill_name=candidate.name,
            args=args,
            ctx=ctx,
            runtime=self.runtime,
        )
        if result.files:
            for filename, content in result.files.items():
                await ctx.reply_document(document=content, filename=filename)

        if result.ok:
            if todo_session:
                todo_session.mark_step("act", "done", f"Extension `{candidate.name}` done.")
                todo_session.mark_step("verify", "done", "Direct extension returned successfully.")
            self._record_tool_profile(
                name=str(getattr(candidate, "tool_name", "") or ""),
                result={"ok": True},
                started=started,
            )
            return self._sanitize_skill_text(result.text or success_fallback)

        error = result.message or result.error_code or "unknown_error"
        self._record_tool_profile(
            name=str(getattr(candidate, "tool_name", "") or ""),
            result={"ok": False, "message": error},
            started=started,
        )
        if todo_session:
            todo_session.mark_step("act", "blocked", f"Extension `{candidate.name}` failed.")
            todo_session.mark_step("deliver", "blocked", error)
        return f"❌ {failure_prefix}：{error}"

    def _is_followup_detail_reply(self, text: str) -> bool:
        lowered = (text or "").lower().strip()
        if not lowered:
            return False
        if len(lowered) > 140:
            return False
        cues = (
            "我指的是",
            "就是",
            "就这个",
            "按这个",
            "latest",
            "最新",
            "这个",
            "that one",
        )
        return any(cue in lowered for cue in cues)

    def _extract_deployment_service(self, text: str) -> str:
        lowered = (text or "").lower()
        if not lowered:
            return ""

        patterns = [
            r"(?:部署|安装|搭建|启动)\s*(?:一套|一个|个|套)?\s*([a-zA-Z0-9._\-\u4e00-\u9fff]+(?:\s+[a-zA-Z0-9._\-\u4e00-\u9fff]+)?)",
            r"(?:deploy|install|setup)\s+([a-zA-Z0-9._\-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                candidate = match.group(1).strip(" ,，。.!！?？")
                candidate = re.sub(r"(服务|系统|平台)$", "", candidate)
                candidate = re.sub(r"\s+", "-", candidate.strip())
                if candidate:
                    return candidate.lower()

        return ""

    def _sanitize_skill_text(self, text: str) -> str:
        if text.startswith("🔇🔇🔇"):
            return text[3:].lstrip()
        return text

    def _extract_last_user_text(self, message_history: list) -> str:
        for msg in reversed(message_history):
            if isinstance(msg, dict):
                role = msg.get("role")
                parts = msg.get("parts", [])
            else:
                role = getattr(msg, "role", None)
                parts = getattr(msg, "parts", [])

            if role != "user":
                continue

            texts: List[str] = []
            for part in parts:
                if isinstance(part, dict) and "text" in part:
                    texts.append(str(part["text"]))
                elif hasattr(part, "text") and part.text:
                    texts.append(str(part.text))
            return "\n".join(texts).strip()
        return ""

    def _extract_recent_user_text(
        self,
        message_history: list,
        max_messages: int = 3,
        max_chars: int = 1200,
    ) -> str:
        """Extract recent user messages for extension routing continuity."""
        if max_messages < 1:
            max_messages = 1

        collected: List[str] = []
        for msg in reversed(message_history):
            if isinstance(msg, dict):
                role = msg.get("role")
                parts = msg.get("parts", [])
            else:
                role = getattr(msg, "role", None)
                parts = getattr(msg, "parts", [])

            if role != "user":
                continue

            texts: List[str] = []
            for part in parts:
                if isinstance(part, dict) and "text" in part and part.get("text"):
                    texts.append(str(part["text"]))
                elif hasattr(part, "text") and part.text:
                    texts.append(str(part.text))

            if texts:
                collected.append("\n".join(texts).strip())
            if len(collected) >= max_messages:
                break

        if not collected:
            return ""

        collected.reverse()
        merged = "\n".join([item for item in collected if item])
        if len(merged) > max_chars:
            merged = merged[-max_chars:]
        return merged

    async def _get_memory_tool_definitions(self, user_id: int):
        if self._memory_tools_cache:
            return self._memory_tools_cache

        try:
            from mcp_client import mcp_manager
            from mcp_client.memory import register_memory_server
            from mcp_client.tools_bridge import convert_mcp_tools_to_gemini

            register_memory_server()
            memory_server = await mcp_manager.get_server("memory", user_id=user_id)
            if memory_server and memory_server.session:
                mcp_tools_result = await memory_server.session.list_tools()
                self._memory_tools_cache = convert_mcp_tools_to_gemini(mcp_tools_result.tools)
                return self._memory_tools_cache
        except Exception as exc:
            logger.error("Failed to fetch memory tools: %s", exc)

        return None

    async def _get_active_memory_server(self, user_id: int):
        try:
            from mcp_client import mcp_manager
            from mcp_client.memory import register_memory_server

            register_memory_server()
            return await mcp_manager.get_server("memory", user_id=user_id)
        except Exception:
            return None

    def _is_memory_tool(self, name: str) -> bool:
        if not self._memory_tools_cache:
            return False

        for tool in self._memory_tools_cache:
            if isinstance(tool, dict) and tool.get("name") == name:
                return True
            if hasattr(tool, "name") and tool.name == name:
                return True
        return False


agent_orchestrator = AgentOrchestrator()
