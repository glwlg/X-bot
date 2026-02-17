import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, List

from core.config import (
    DATA_DIR,
    X_DEPLOYMENT_STAGING_PATH,
    SERVER_IP,
    AUTO_RECOVERY_MAX_ATTEMPTS,
)
from core.extension_executor import ExtensionExecutor
from core.extension_router import ExtensionCandidate, ExtensionRouter
from core.heartbeat_store import heartbeat_store
from core.orchestrator_context import OrchestratorRuntimeContext
from core.orchestrator_event_handler import OrchestratorEventHandler
from core.platform.models import UnifiedContext
from core.primitive_runtime import PrimitiveRuntime
from core.prompt_composer import prompt_composer
from core.orchestrator_runtime_tools import RuntimeToolAssembler, ToolCallDispatcher
from core.skill_loader import skill_loader
from core.task_inbox import task_inbox
from core.task_manager import task_manager
from core.tool_access_store import tool_access_store
from core.tool_broker import ToolBroker
from core.tool_profile_store import tool_profile_store
from core.tools.dispatch_tools import (
    dispatch_tools,
)  # compatibility export for tests/hooks
from services.ai_service import AiService

logger = logging.getLogger(__name__)


def _sanitize_manager_text(text: str, worker_labels: Dict[str, str]) -> str:
    raw = str(text or "")
    if not raw or not worker_labels:
        return raw

    cleaned = raw
    ordered = sorted(
        (
            (str(worker_id or "").strip(), str(name or "").strip())
            for worker_id, name in worker_labels.items()
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    ordered = [(worker_id, name) for worker_id, name in ordered if worker_id]
    if not ordered:
        return raw

    primary_name = next(
        (name for _worker_id, name in ordered if name),
        ordered[0][0],
    )

    for worker_id, worker_name in ordered:
        display_name = worker_name or primary_name
        cleaned = cleaned.replace(f"`{worker_id}`", display_name)
        cleaned = cleaned.replace(worker_id, display_name)

    cleaned = re.sub(r"\bworker_id\b", "执行助手编号", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bbackend\b", "执行方式", cleaned, flags=re.IGNORECASE)
    if "worker" not in primary_name.lower():
        cleaned = re.sub(
            r"\bworkers\b", f"{primary_name}团队", cleaned, flags=re.IGNORECASE
        )
        cleaned = re.sub(r"\bworker\b", primary_name, cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bWorker\b", primary_name, cleaned)
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
        self.extension_executor = ExtensionExecutor()
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

        dispatched_worker_labels: Dict[str, str] = {}
        last_user_text = self._extract_last_user_text(message_history)
        routing_text = self._extract_recent_user_text(message_history, max_messages=3)
        if not routing_text:
            routing_text = last_user_text
        task_goal = last_user_text or routing_text

        task_id = runtime_ctx.task_id
        task_inbox_id = runtime_ctx.task_inbox_id
        todo_session = _NoopTodoSession(str(user_id))

        append_session_event = runtime_ctx.append_session_event
        update_session_task = runtime_ctx.update_session_task
        update_task_inbox_status = runtime_ctx.update_task_inbox_status

        await runtime_ctx.mark_manager_loop_started(task_goal)

        logger.info(
            "Extension routing text (trimmed): %s",
            routing_text.replace("\n", " | ")[:300],
        )
        raw_extension_candidates = self.extension_router.route(
            routing_text, max_candidates=24
        )
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
            candidate_text = ", ".join(
                [candidate.name for candidate in extension_candidates]
            )
            todo_session.mark_step("plan", "done", f"Candidates: {candidate_text}")
        else:
            todo_session.mark_step(
                "plan", "done", "No extension matched; primitives only."
            )

        task_workspace_root = self._resolve_task_workspace_root(
            extension_candidates=extension_candidates,
            intent_text=routing_text or last_user_text,
        )
        await runtime_ctx.activate_session(
            task_goal=task_goal,
            task_workspace_root=task_workspace_root,
        )

        tooling_assembler = RuntimeToolAssembler(
            runtime_user_id=user_id_str,
            platform_name=platform_name,
            runtime_tool_allowed=self._runtime_tool_allowed,
        )
        tools = await tooling_assembler.assemble(extension_candidates)

        def on_worker_dispatched(worker_id: str, worker_name: str) -> None:
            dispatched_worker_id = str(worker_id or "").strip()
            dispatched_worker_name = str(worker_name or "").strip()
            if dispatched_worker_id:
                dispatched_worker_labels[dispatched_worker_id] = (
                    dispatched_worker_name or dispatched_worker_id
                )
            if dispatched_worker_name:
                user_data["last_dispatched_worker_name"] = dispatched_worker_name

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
            record_tool_profile=self._record_tool_profile,
            todo_mark_step=todo_session.mark_step,
            append_session_event=append_session_event,
            on_worker_dispatched=on_worker_dispatched,
        )
        tool_dispatcher.set_extension_candidates(extension_candidates)
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
                self._record_tool_profile(
                    name=name,
                    result=error_result,
                    started=started,
                )
                return error_result

        system_instruction = self._build_system_instruction(
            extension_candidates,
            intent_text=routing_text or last_user_text,
            runtime_user_id=user_id_str,
            runtime_policy_ctx=runtime_policy_ctx,
            tools=tools,
        )

        suppressed_max_turn_warning = ""
        max_recovery_attempts = max(1, int(AUTO_RECOVERY_MAX_ATTEMPTS))

        def sanitize_preview(text: str) -> str:
            if manager_runtime:
                return _sanitize_manager_text(text, dispatched_worker_labels)
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

        async def on_agent_event(event: str, payload: Dict[str, Any]):
            return await event_handler.handle(event, payload)

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
                yield _sanitize_manager_text(chunk, dispatched_worker_labels)
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
                reroute_candidates = self.extension_router.route(
                    routing_text, max_candidates=24
                )
                extension_candidates = self._apply_extension_candidate_policy(
                    reroute_candidates,
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
                extension_candidates = self._rank_extension_candidates(
                    extension_candidates
                )
                tools = await tooling_assembler.assemble(extension_candidates)
                tool_dispatcher.set_extension_candidates(extension_candidates)
                tool_dispatcher.set_available_tool_names(
                    tooling_assembler.tool_names(tools)
                )

                system_instruction = self._build_system_instruction(
                    extension_candidates,
                    intent_text=routing_text or last_user_text,
                    runtime_user_id=user_id_str,
                    runtime_policy_ctx=runtime_policy_ctx,
                    tools=tools,
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
                        yield _sanitize_manager_text(chunk, dispatched_worker_labels)
                    else:
                        yield chunk

            if event_handler.flags.blocked and suppressed_max_turn_warning:
                yield suppressed_max_turn_warning
        elif event_handler.flags.blocked and suppressed_max_turn_warning:
            yield suppressed_max_turn_warning

        if not event_handler.flags.blocked and not event_handler.flags.completed:
            todo_session.mark_completed("Conversation loop completed.")
            if runtime_ctx.session_state_active:
                await update_session_task(
                    status="done",
                    result_summary="Conversation loop completed.",
                    needs_confirmation=False,
                    confirmation_deadline="",
                    clear_active=True,
                )
                await append_session_event(f"conversation_completed:{task_id}")
            if task_inbox_id:
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
        runtime_policy_ctx: Dict[str, Any] | None = None,
        tools: List[Dict[str, Any]] | None = None,
    ) -> str:
        del extension_candidates
        del intent_text
        agent_kind = (
            str((runtime_policy_ctx or {}).get("agent_kind") or "").strip().lower()
        )
        mode = "worker" if agent_kind == "worker" else "manager"
        return prompt_composer.compose_base(
            runtime_user_id=runtime_user_id,
            tools=tools or [],
            runtime_policy_ctx=runtime_policy_ctx or {},
            mode=mode,
        )

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
        if (
            "deployment_manager" not in candidate_names
            and not self._is_deployment_intent(intent_text)
        ):
            return ""

        resolved = os.path.abspath(os.path.expanduser(staging_path))
        try:
            os.makedirs(resolved, exist_ok=True)
        except Exception:
            pass
        return resolved

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

    def _apply_extension_candidate_policy(
        self,
        extension_candidates: list,
        intent_text: str = "",
    ) -> list:
        del intent_text
        deduped: List[ExtensionCandidate] = []
        seen_names: set[str] = set()
        for candidate in extension_candidates or []:
            name = getattr(candidate, "name", "")
            if not name or name in seen_names:
                continue
            deduped.append(candidate)
            seen_names.add(name)
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
            log_level = (
                logging.DEBUG
                if detail.get("reason") == "worker_memory_disabled"
                else logging.INFO
            )
            logger.log(
                log_level,
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
            return True, self._sanitize_skill_text(
                result.text or "🛠️ 自动技能进化完成。"
            )

        message = result.message or result.error_code or "unknown_error"
        if todo_session:
            todo_session.heartbeat(f"auto_evolution:failed:{message}")
            todo_session.mark_step(
                "act",
                "blocked",
                f"Automatic skill evolution failed: {message}",
            )
        return False, f"⚠️ 自动技能进化失败：{message}"

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


agent_orchestrator = AgentOrchestrator()
