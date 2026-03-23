import asyncio
import inspect
import logging
import os
import re
import time
from typing import Any, Dict, List, cast

from core.channel_runtime_store import channel_runtime_store
from core.config import (
    X_DEPLOYMENT_STAGING_PATH,
    AUTO_RECOVERY_MAX_ATTEMPTS,
)
from core.extension_router import ExtensionCandidate, ExtensionRouter
from core.heartbeat_store import heartbeat_store
from core.orchestrator_context import OrchestratorRuntimeContext
from core.orchestrator_event_handler import OrchestratorEventHandler
from core.platform.models import UnifiedContext
from core.primitive_runtime import PrimitiveRuntime
from core.prompt_composer import prompt_composer
from core.orchestrator_runtime_tools import RuntimeToolAssembler, ToolCallDispatcher
from core.runtime_callbacks import get_runtime_callback
from core.task_inbox import task_inbox
from core.task_manager import task_manager
from core.tool_access_store import tool_access_store
from core.tool_broker import ToolBroker
from services.ai_service import AiService
from services.intent_router import intent_router

logger = logging.getLogger(__name__)


def _sanitize_manager_text(text: str, subagent_labels: Dict[str, str]) -> str:
    raw = str(text or "")
    if not raw or not subagent_labels:
        return raw

    cleaned = raw
    ordered = sorted(
        (
            (str(subagent_id or "").strip(), str(name or "").strip())
            for subagent_id, name in subagent_labels.items()
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    ordered = [(subagent_id, name) for subagent_id, name in ordered if subagent_id]
    if not ordered:
        return raw

    primary_name = next(
        (name for _subagent_id, name in ordered if name),
        ordered[0][0],
    )

    for subagent_id, subagent_name in ordered:
        display_name = subagent_name or primary_name
        cleaned = cleaned.replace(f"`{subagent_id}`", display_name)
        cleaned = cleaned.replace(subagent_id, display_name)

    cleaned = re.sub(r"\bsubagent_id\b", "执行助手编号", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bbackend\b", "执行方式", cleaned, flags=re.IGNORECASE)
    if "subagent" not in primary_name.lower():
        cleaned = re.sub(
            r"\bsubagents\b", f"{primary_name}团队", cleaned, flags=re.IGNORECASE
        )
        cleaned = re.sub(r"\bsubagent\b", primary_name, cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bSubagent\b", primary_name, cleaned)
    return cleaned


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
        self.auto_evolve_enabled = (
            os.getenv("AUTO_EVOLVE_ON_BLOCK", "false").lower() == "true"
        )
        logger.info(
            "Orchestrator policy: auto_evolve_enabled=%s",
            self.auto_evolve_enabled,
        )

    async def handle_message(self, ctx: UnifiedContext, message_history: list):
        runtime_ctx = OrchestratorRuntimeContext.from_message(ctx)
        user_id = runtime_ctx.user_id
        user_data = runtime_ctx.user_data
        user_id_str = runtime_ctx.runtime_user_id
        platform_name = runtime_ctx.platform_name
        runtime_policy_ctx = runtime_ctx.runtime_policy_ctx
        manager_runtime = runtime_ctx.manager_runtime
        explicit_allowed_skill_names = {
            str(item or "").strip()
            for item in list(user_data.get("allowed_skill_names") or [])
            if str(item or "").strip()
        }
        explicit_allowed_tool_names = {
            str(item or "").strip()
            for item in list(user_data.get("allowed_tool_names") or [])
            if str(item or "").strip()
        }
        if not explicit_allowed_tool_names:
            explicit_allowed_tool_names = set()

        dispatched_subagent_labels: Dict[str, str] = {}
        last_user_text = self._extract_last_user_text(message_history)
        routing_text = self._extract_recent_user_text(message_history, max_messages=3)
        if not routing_text:
            routing_text = last_user_text
        task_goal = last_user_text or routing_text

        task_id = runtime_ctx.task_id
        todo_session = _NoopTodoSession(str(user_id))

        logger.info(
            "Extension routing text (trimmed): %s",
            routing_text.replace("\n", " | ")[:300],
        )
        (
            raw_extension_candidates,
            extension_candidates,
            routing_decision,
        ) = await self._resolve_extension_candidates(
            message_history=message_history,
            routing_text=routing_text,
            last_user_text=last_user_text,
            runtime_user_id=user_id_str,
            platform_name=platform_name,
            explicit_allowed_skill_names=explicit_allowed_skill_names,
        )
        allowed_skill_names = (
            set(explicit_allowed_skill_names)
            if explicit_allowed_skill_names
            else {candidate.name for candidate in extension_candidates}
        )
        request_mode = str(routing_decision.request_mode or "").strip().lower() or "chat"
        task_tracking_requested = (
            bool(routing_decision.task_tracking)
            if routing_decision.task_tracking is not None
            else request_mode == "task"
        )
        logger.info(
            "Extension candidates selected: raw=%s filtered=%s request_mode=%s task_tracking=%s routed=%s confidence=%.2f reason=%s",
            [candidate.name for candidate in raw_extension_candidates] or "none",
            [candidate.name for candidate in extension_candidates] or "none",
            request_mode,
            task_tracking_requested,
            routing_decision.candidate_skills or "none",
            float(routing_decision.confidence),
            routing_decision.reason,
        )
        if extension_candidates:
            candidate_text = ", ".join(
                [candidate.name for candidate in extension_candidates]
            )
            todo_session.mark_step("plan", "done", f"Candidates: {candidate_text}")
        else:
            todo_session.mark_step(
                "plan", "done", "No extension matched; primitives only."
            )

        async def _noop_append_session_event(_note: str) -> None:
            return None

        async def _noop_update_session_task(**_kwargs: Any) -> None:
            return None

        async def _noop_update_task_inbox_status(**_kwargs: Any) -> None:
            return None

        task_tracking_enabled = (
            runtime_ctx.session_state_enabled
            and task_tracking_requested
        )
        append_session_event = (
            runtime_ctx.append_session_event
            if task_tracking_enabled
            else _noop_append_session_event
        )
        update_session_task = (
            runtime_ctx.update_session_task
            if task_tracking_enabled
            else _noop_update_session_task
        )
        update_task_inbox_status = (
            runtime_ctx.update_task_inbox_status
            if task_tracking_enabled
            else _noop_update_task_inbox_status
        )
        task_inbox_id = ""
        if task_tracking_enabled:
            await runtime_ctx.ensure_task_inbox(task_goal=task_goal)
            task_inbox_id = runtime_ctx.task_inbox_id
            await runtime_ctx.mark_manager_loop_started(task_goal)
        logger.info(
            "Task tracking decision: enabled=%s mode=%s requested=%s task_inbox_id=%s",
            task_tracking_enabled,
            request_mode,
            task_tracking_requested,
            task_inbox_id or "none",
        )

        task_workspace_root = self._resolve_task_workspace_root(
            extension_candidates=extension_candidates,
        )
        if task_tracking_enabled:
            await runtime_ctx.activate_session(
                task_goal=task_goal,
                task_workspace_root=task_workspace_root,
            )

        tooling_assembler = RuntimeToolAssembler(
            runtime_user_id=user_id_str,
            platform_name=platform_name,
            runtime_tool_allowed=self._runtime_tool_allowed,
            allowed_skill_names=allowed_skill_names,
            allowed_tool_names=explicit_allowed_tool_names or None,
        )
        tools = await tooling_assembler.assemble()

        tool_dispatcher = ToolCallDispatcher(
            runtime_user_id=user_id_str,
            platform_name=platform_name,
            task_id=str(task_id),
            task_inbox_id=task_inbox_id,
            task_workspace_root=task_workspace_root,
            ctx=ctx,
            runtime=self.runtime,
            tool_broker=self.tool_broker,
            runtime_tool_allowed=self._runtime_tool_allowed,
            todo_mark_step=todo_session.mark_step,
            append_session_event=append_session_event,
            allowed_skill_names=allowed_skill_names,
            allowed_tool_names=explicit_allowed_tool_names or None,
        )
        tool_dispatcher.set_available_tool_names(tooling_assembler.tool_names(tools))

        async def tool_executor(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
            logger.info("Agent invoking tool: %s with args=%s", name, args)
            await append_session_event(f"tool_start:{task_id}:{name}")
            todo_session.mark_step("act", "in_progress", f"Calling tool `{name}`")
            todo_session.heartbeat(f"tool:{name}:start")
            task_manager.heartbeat(user_id, f"tool:{name}:start")
            execution_policy = self.tool_broker.resolve_policy(ctx)
            started = time.perf_counter()

            try:
                return await tool_dispatcher.execute(
                    name=name,
                    args=args,
                    execution_policy=execution_policy,
                    started=started,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Error in tool_executor: %s", exc, exc_info=True)
                todo_session.mark_step(
                    "act", "blocked", f"Tool `{name}` exception: {exc}"
                )
                await append_session_event(
                    f"tool_finish:{task_id}:{name}:exception:{exc}"
                )
                error_result = {
                    "ok": False,
                    "error_code": "system_error",
                    "message": str(exc),
                }
                return error_result

        system_instruction = self._build_system_instruction(
            extension_candidates,
            intent_text=routing_text or last_user_text,
            runtime_user_id=user_id_str,
            platform_name=runtime_ctx.platform_name,
            runtime_policy_ctx=runtime_policy_ctx,
            tools=tools,
            allowed_skill_names_override=allowed_skill_names,
        )

        suppressed_max_turn_warning = ""
        max_recovery_attempts = max(1, int(AUTO_RECOVERY_MAX_ATTEMPTS))

        def sanitize_preview(text: str) -> str:
            if manager_runtime:
                return _sanitize_manager_text(text, dispatched_subagent_labels)
            return text

        event_handler = OrchestratorEventHandler(
            user_id=user_id,
            task_id=str(task_id),
            task_inbox_id=task_inbox_id,
            ctx=ctx,
            todo_session=todo_session,
            manager_runtime=manager_runtime,
            session_state_active=runtime_ctx.session_state_active,
            max_recovery_attempts=max_recovery_attempts,
            sanitize_preview=sanitize_preview,
            build_recovery_instruction=self._build_recovery_instruction,
            append_session_event=append_session_event,
            update_session_task=update_session_task,
            update_task_inbox_status=update_task_inbox_status,
        )

        subagent_progress_hook = get_runtime_callback(ctx, "subagent_progress_callback")
        if not callable(subagent_progress_hook):
            subagent_progress_hook = user_data.get("subagent_progress_callback")
        if not callable(subagent_progress_hook):
            subagent_progress_hook = None
        manager_progress_hook = get_runtime_callback(ctx, "manager_progress_callback")
        if not callable(manager_progress_hook):
            manager_progress_hook = user_data.get("manager_progress_callback")
        if not callable(manager_progress_hook):
            manager_progress_hook = None
        progress_steps_raw = user_data.get("subagent_progress_steps")
        progress_steps: list[dict[str, Any]] = (
            [item for item in progress_steps_raw if isinstance(item, dict)]
            if isinstance(progress_steps_raw, list)
            else []
        )

        async def emit_subagent_progress(event: str, payload: Dict[str, Any]) -> None:
            if subagent_progress_hook is None:
                return
            try:
                event_name = str(event or "").strip().lower()
                turn = int(payload.get("turn") or 0)
                if event_name == "tool_call_started":
                    tool_name = str(payload.get("name") or "").strip()
                    if tool_name:
                        progress_steps.append(
                            {
                                "name": tool_name,
                                "status": "running",
                                "summary": "",
                                "turn": turn,
                            }
                        )
                elif event_name == "tool_call_finished":
                    tool_name = str(payload.get("name") or "").strip()
                    tool_ok = bool(payload.get("ok"))
                    summary = str(payload.get("summary") or "").strip()
                    updated = False
                    for idx in range(len(progress_steps) - 1, -1, -1):
                        row = progress_steps[idx]
                        if str(row.get("name") or "") != tool_name:
                            continue
                        if str(row.get("status") or "") != "running":
                            continue
                        row["status"] = "done" if tool_ok else "failed"
                        row["summary"] = summary[:160]
                        row["turn"] = turn
                        updated = True
                        break
                    if not updated and tool_name:
                        progress_steps.append(
                            {
                                "name": tool_name,
                                "status": "done" if tool_ok else "failed",
                                "summary": summary[:160],
                                "turn": turn,
                            }
                        )
                elif event_name == "final_response":
                    user_data["subagent_progress_final_preview"] = str(
                        payload.get("text_preview") or ""
                    )[:180]

                progress_steps[:] = progress_steps[-20:]
                user_data["subagent_progress_steps"] = progress_steps

                running_tool = ""
                done_tools: list[str] = []
                failed_tools: list[str] = []
                for row in progress_steps:
                    name = str(row.get("name") or "").strip()
                    status = str(row.get("status") or "").strip().lower()
                    if not name:
                        continue
                    if status == "running":
                        running_tool = name
                    elif status == "done":
                        if name not in done_tools:
                            done_tools.append(name)
                    elif status == "failed":
                        if name not in failed_tools:
                            failed_tools.append(name)

                snapshot = {
                    "event": event_name,
                    "turn": turn,
                    "updated_at": time.time(),
                    "running_tool": running_tool,
                    "done_tools": done_tools[-5:],
                    "failed_tools": failed_tools[-3:],
                    "recent_steps": progress_steps[-6:],
                    "final_preview": str(
                        user_data.get("subagent_progress_final_preview") or ""
                    )[:180],
                }
                if event_name in {"tool_call_started", "tool_call_finished"}:
                    snapshot["name"] = str(payload.get("name") or "").strip()
                if event_name == "tool_call_started":
                    args = payload.get("args")
                    if isinstance(args, dict):
                        snapshot["args"] = dict(args)
                elif event_name == "tool_call_finished":
                    snapshot["ok"] = bool(payload.get("ok"))
                    snapshot["summary"] = str(payload.get("summary") or "").strip()
                    snapshot["terminal"] = bool(payload.get("terminal"))
                    snapshot["task_outcome"] = str(
                        payload.get("task_outcome") or ""
                    ).strip()
                    snapshot["failure_mode"] = str(
                        payload.get("failure_mode") or ""
                    ).strip()
                    terminal_payload = payload.get("terminal_payload")
                    if isinstance(terminal_payload, dict) and terminal_payload:
                        snapshot["terminal_payload"] = dict(terminal_payload)
                    terminal_text = str(payload.get("terminal_text") or "").strip()
                    if terminal_text:
                        snapshot["terminal_text"] = terminal_text

                maybe_coro = subagent_progress_hook(snapshot)
                if inspect.isawaitable(maybe_coro):
                    await cast(Any, maybe_coro)
            except Exception as exc:
                logger.debug("subagent progress hook error: %s", exc)

        async def emit_manager_progress(event: str, payload: Dict[str, Any]) -> None:
            if not manager_runtime or manager_progress_hook is None:
                return
            try:
                snapshot = dict(payload or {})
                snapshot["event"] = str(event or "").strip().lower()
                snapshot.setdefault("task_id", str(task_id))
                maybe_coro = manager_progress_hook(snapshot)
                if inspect.isawaitable(maybe_coro):
                    await cast(Any, maybe_coro)
            except Exception as exc:
                logger.debug("manager progress hook error: %s", exc)

        async def on_agent_event(event: str, payload: Dict[str, Any]):
            directive = await event_handler.handle(event, payload)
            await emit_manager_progress(event, payload)
            await emit_subagent_progress(event, payload)
            return directive

        logger.info("final tools: %s", tools)
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
            if manager_runtime and isinstance(chunk, str):
                yield _sanitize_manager_text(chunk, dispatched_subagent_labels)
            else:
                yield chunk

        if (
            event_handler.flags.blocked
            and event_handler.flags.blocked_reason == "max_turn_limit"
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
                (
                    reroute_candidates,
                    extension_candidates,
                    routing_decision,
                ) = await self._resolve_extension_candidates(
                    message_history=message_history,
                    routing_text=routing_text,
                    last_user_text=last_user_text,
                    runtime_user_id=user_id_str,
                    platform_name=platform_name,
                    explicit_allowed_skill_names=explicit_allowed_skill_names,
                )
                allowed_skill_names = {
                    candidate.name for candidate in extension_candidates
                }
                if explicit_allowed_skill_names:
                    allowed_skill_names = set(explicit_allowed_skill_names)
                logger.info(
                    "Extension candidates after evolution: raw=%s filtered=%s request_mode=%s task_tracking=%s routed=%s confidence=%.2f reason=%s",
                    [candidate.name for candidate in reroute_candidates] or "none",
                    [candidate.name for candidate in extension_candidates] or "none",
                    routing_decision.request_mode,
                    routing_decision.task_tracking,
                    routing_decision.candidate_skills or "none",
                    float(routing_decision.confidence),
                    routing_decision.reason,
                )
                tooling_assembler.allowed_skill_names = set(allowed_skill_names)
                tool_dispatcher.allowed_skill_names = set(allowed_skill_names)
                tooling_assembler.allowed_tool_names = (
                    set(explicit_allowed_tool_names)
                    if explicit_allowed_tool_names
                    else None
                )
                tool_dispatcher.allowed_tool_names = (
                    set(explicit_allowed_tool_names)
                    if explicit_allowed_tool_names
                    else None
                )
                tools = await tooling_assembler.assemble()
                tool_dispatcher.set_available_tool_names(
                    tooling_assembler.tool_names(tools)
                )

                system_instruction = self._build_system_instruction(
                    extension_candidates,
                    intent_text=routing_text or last_user_text,
                    runtime_user_id=user_id_str,
                    platform_name=runtime_ctx.platform_name,
                    runtime_policy_ctx=runtime_policy_ctx,
                    tools=tools,
                    allowed_skill_names_override=allowed_skill_names,
                )

                event_handler.flags.blocked = False
                event_handler.flags.completed = False
                event_handler.flags.blocked_reason = ""
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
                    if manager_runtime and isinstance(chunk, str):
                        yield _sanitize_manager_text(chunk, dispatched_subagent_labels)
                    else:
                        yield chunk

            if event_handler.flags.blocked and suppressed_max_turn_warning:
                yield suppressed_max_turn_warning
        elif event_handler.flags.blocked and suppressed_max_turn_warning:
            yield suppressed_max_turn_warning

        if not event_handler.flags.blocked and not event_handler.flags.completed:
            todo_session.mark_completed("Conversation loop completed.")
            if runtime_ctx.session_state_active:
                current = channel_runtime_store.get_active_task(
                    platform=runtime_ctx.platform_name,
                    platform_user_id=str(user_id),
                )
                if not current:
                    current = await heartbeat_store.get_session_active_task(str(user_id))
                current_status = str((current or {}).get("status", "")).strip().lower()
                if current_status == "waiting_external":
                    await update_session_task(
                        status="waiting_external",
                        result_summary="Conversation loop completed.",
                        needs_confirmation=False,
                        confirmation_deadline="",
                    )
                else:
                    await update_session_task(
                        status="done",
                        result_summary="Conversation loop completed.",
                        needs_confirmation=False,
                        confirmation_deadline="",
                        clear_active=True,
                    )
                await append_session_event(f"conversation_completed:{task_id}")
            if task_inbox_id:
                current_task = await task_inbox.get(task_inbox_id)
                current_task_status = (
                    str((current_task or {}).status if current_task else "")
                    .strip()
                    .lower()
                )
                auto_followup = event_handler._maybe_pr_followup_metadata(
                    "Conversation loop completed."
                )
                if auto_followup and current_task_status not in {
                    "waiting_external",
                    "completed",
                }:
                    await task_inbox.update_status(
                        task_inbox_id,
                        "waiting_external",
                        event="conversation_auto_followup_waiting",
                        detail=str(auto_followup.get("detail") or "")[:180],
                        metadata={
                            "followup": dict(auto_followup.get("followup") or {})
                        },
                        result={"manager_mode": "conversation_completed"},
                        output={"text": "Conversation loop completed."},
                    )
                    current_task_status = "waiting_external"
                if current_task_status == "waiting_external":
                    await task_inbox.update_status(
                        task_inbox_id,
                        "waiting_external",
                        event="conversation_kept_open",
                        detail="Conversation loop completed.",
                        result={"manager_mode": "conversation_completed"},
                        output={"text": "Conversation loop completed."},
                    )
                else:
                    await task_inbox.complete(
                        task_inbox_id,
                        result={"manager_mode": "conversation_completed"},
                        final_output="Conversation loop completed.",
                    )

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
        platform_name: str = "",
        runtime_policy_ctx: Dict[str, Any] | None = None,
        tools: List[Dict[str, Any]] | None = None,
        allowed_skill_names_override: set[str] | None = None,
    ) -> str:
        del intent_text
        agent_kind = (
            str((runtime_policy_ctx or {}).get("agent_kind") or "").strip().lower()
        )
        if agent_kind == "subagent":
            mode = "subagent"
        else:
            mode = "manager"
        allowed_skill_names = (
            sorted(
                {
                    str(item or "").strip()
                    for item in list(allowed_skill_names_override or [])
                    if str(item or "").strip()
                }
            )
            if allowed_skill_names_override
            else [
                str(getattr(item, "name", "") or "").strip()
                for item in list(extension_candidates or [])
                if str(getattr(item, "name", "") or "").strip()
            ]
        )
        return prompt_composer.compose_base(
            runtime_user_id=runtime_user_id,
            platform="subagent_kernel" if agent_kind == "subagent" else platform_name,
            tools=tools or [],
            runtime_policy_ctx=runtime_policy_ctx or {},
            mode=mode,
            allowed_skill_names=allowed_skill_names,
        )

    def _resolve_task_workspace_root(
        self,
        extension_candidates: list,
    ) -> str:
        staging_path = (X_DEPLOYMENT_STAGING_PATH or "").strip()
        if not staging_path:
            return ""

        if not self._extension_candidates_include_group(
            extension_candidates,
            "group:ops",
        ):
            return ""

        resolved = os.path.abspath(os.path.expanduser(staging_path))
        try:
            os.makedirs(resolved, exist_ok=True)
        except Exception:
            pass
        return resolved

    def _extension_candidates_include_group(
        self,
        extension_candidates: list,
        group_name: str,
    ) -> bool:
        normalized_group = str(group_name or "").strip().lower()
        if not normalized_group:
            return False
        for candidate in extension_candidates or []:
            skill_name = str(getattr(candidate, "name", "") or "").strip()
            if not skill_name:
                continue
            groups = tool_access_store.groups_for_tool(skill_name, kind="tool")
            if normalized_group in {
                str(item or "").strip().lower() for item in groups
            }:
                return True
        return False

    def _apply_extension_candidate_policy(
        self,
        extension_candidates: list,
    ) -> list:
        deduped: List[ExtensionCandidate] = []
        seen_names: set[str] = set()
        for candidate in extension_candidates or []:
            name = getattr(candidate, "name", "")
            if not name or name in seen_names:
                continue
            deduped.append(candidate)
            seen_names.add(name)
        return deduped

    async def _resolve_extension_candidates(
        self,
        *,
        message_history: list,
        routing_text: str,
        last_user_text: str,
        runtime_user_id: str,
        platform_name: str,
        explicit_allowed_skill_names: set[str] | None = None,
    ) -> tuple[list[ExtensionCandidate], list[ExtensionCandidate], Any]:
        raw_extension_candidates = self.extension_router.route(
            routing_text, max_candidates=24
        )
        extension_candidates = self._apply_extension_candidate_policy(
            raw_extension_candidates,
        )
        extension_candidates = [
            candidate
            for candidate in extension_candidates
            if self._runtime_tool_allowed(
                runtime_user_id=runtime_user_id,
                platform=platform_name,
                tool_name=candidate.tool_name,
                kind="tool",
            )
        ]
        if explicit_allowed_skill_names:
            extension_candidates = [
                candidate
                for candidate in extension_candidates
                if candidate.name in explicit_allowed_skill_names
            ]

        routing_decision = await intent_router.route(
            dialog_messages=self._extract_recent_dialog_messages(
                message_history,
                max_messages=10,
            ),
            candidates=extension_candidates,
            max_candidates=5,
        )
        selected = set(routing_decision.candidate_skills)
        extension_candidates = [
            candidate for candidate in extension_candidates if candidate.name in selected
        ]
        if explicit_allowed_skill_names:
            extension_candidates = [
                candidate
                for candidate in extension_candidates
                if candidate.name in explicit_allowed_skill_names
            ]
        return raw_extension_candidates, extension_candidates, routing_decision

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

    def _should_auto_evolve(
        self,
        intent_text: str,
        extension_candidates: list,
    ) -> bool:
        del intent_text
        if not self.auto_evolve_enabled:
            return False
        return self._extension_candidates_include_group(
            extension_candidates,
            "group:skill-admin",
        )

    async def _attempt_auto_skill_evolution(
        self,
        ctx: UnifiedContext,
        user_request: str,
        todo_session: Any | None = None,
    ) -> tuple[bool, str]:
        # Temporarily disabled as extension executor is removed
        # Can be re-implemented natively with prompts/SOPs in the future
        return False, "Evolution temporarily disabled."

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
                else:
                    part_text = getattr(part, "text", None)
                    if part_text:
                        texts.append(str(part_text))
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
                else:
                    part_text = getattr(part, "text", None)
                    if part_text:
                        texts.append(str(part_text))

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

    def _extract_recent_dialog_messages(
        self,
        message_history: list,
        *,
        max_messages: int = 10,
    ) -> list[dict[str, str]]:
        if max_messages < 1:
            max_messages = 1

        collected: list[dict[str, str]] = []
        for msg in reversed(message_history):
            if isinstance(msg, dict):
                role = str(msg.get("role") or "").strip().lower()
                parts = msg.get("parts", [])
            else:
                role = str(getattr(msg, "role", "") or "").strip().lower()
                parts = getattr(msg, "parts", [])

            if role not in {"user", "assistant", "model"}:
                continue

            texts: List[str] = []
            for part in parts:
                if isinstance(part, dict) and part.get("text"):
                    texts.append(str(part["text"]))
                else:
                    part_text = getattr(part, "text", None)
                    if part_text:
                        texts.append(str(part_text))
            merged = "\n".join([item for item in texts if item]).strip()
            if not merged:
                continue
            collected.append(
                {
                    "role": "assistant" if role == "model" else role,
                    "content": merged,
                }
            )
            if len(collected) >= max_messages:
                break

        collected.reverse()
        return collected


agent_orchestrator = AgentOrchestrator()
