from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

from core.channel_runtime_store import channel_runtime_store
from core.heartbeat_store import heartbeat_store
from core.task_inbox import task_inbox
from core.task_manager import task_manager

AppendSessionEvent = Callable[[str], Awaitable[None]]
UpdateSessionTask = Callable[..., Awaitable[None]]
UpdateTaskInboxStatus = Callable[..., Awaitable[None]]
SanitizePreview = Callable[[str], str]
BuildRecoveryInstruction = Callable[[int, int, list[str] | None], str]


@dataclass
class EventLoopFlags:
    blocked: bool = False
    completed: bool = False
    blocked_reason: str = ""
    recovery_attempts: int = 0
    saw_tool_call: bool = False


class OrchestratorEventHandler:
    """Handle ai_service event callbacks for one orchestrator loop."""

    def __init__(
        self,
        *,
        user_id: Any,
        task_id: str,
        task_inbox_id: str,
        task_goal: str,
        request_mode: str,
        ctx: Any,
        todo_session: Any,
        ikaros_runtime: bool,
        session_state_active: bool,
        max_recovery_attempts: int,
        sanitize_preview: SanitizePreview,
        build_recovery_instruction: BuildRecoveryInstruction,
        append_session_event: AppendSessionEvent,
        update_session_task: UpdateSessionTask,
        update_task_inbox_status: UpdateTaskInboxStatus,
    ):
        self.user_id = user_id
        self.task_id = str(task_id)
        self.task_inbox_id = str(task_inbox_id or "").strip()
        self.task_goal = str(task_goal or "").strip()
        self.request_mode = str(request_mode or "").strip().lower() or "chat"
        self.ctx = ctx
        self.todo_session = todo_session
        self.ikaros_runtime = bool(ikaros_runtime)
        self.session_state_active = bool(session_state_active)
        self.max_recovery_attempts = max(1, int(max_recovery_attempts or 1))
        self.sanitize_preview = sanitize_preview
        self.build_recovery_instruction = build_recovery_instruction
        self.append_session_event = append_session_event
        self.update_session_task = update_session_task
        self.update_task_inbox_status = update_task_inbox_status
        self.flags = EventLoopFlags()

    def _append_pending_ui(self, ui_payload: Any, *, replace: bool = False) -> None:
        if not isinstance(ui_payload, dict):
            return
        actions = ui_payload.get("actions")
        if not isinstance(actions, list) or not actions:
            return

        existing = self.ctx.user_data.get("pending_ui")
        blocks: list[dict[str, Any]] = []
        if not replace and isinstance(existing, list):
            for block in existing:
                if isinstance(block, dict):
                    blocks.append(block)
        blocks.append({"actions": actions})
        self.ctx.user_data["pending_ui"] = blocks

    def _require_explicit_completion_signal(self) -> bool:
        return self.request_mode == "task" and self.flags.saw_tool_call

    @staticmethod
    def _extract_completion_signal(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        raw = payload.get("completion_signal")
        if not isinstance(raw, dict):
            terminal_payload = payload.get("terminal_payload")
            if isinstance(terminal_payload, dict):
                raw = terminal_payload.get("completion_signal")
        if not isinstance(raw, dict):
            return {}
        status = str(raw.get("status") or "").strip().lower()
        if not status:
            status = str(payload.get("task_outcome") or "").strip().lower()
        if not status:
            return {}
        signal = dict(raw)
        signal["status"] = status
        signal["explicit"] = bool(raw.get("explicit", True))
        return signal

    async def _build_continue_after_missing_completion_signal(
        self,
        *,
        candidate_text: str,
        origin: str,
    ) -> Dict[str, Any]:
        preview = str(candidate_text or "").strip().replace("\n", " ")[:220]
        summary = preview or "missing structured completion signal"
        self.todo_session.mark_step(
            "verify",
            "in_progress",
            "Waiting for structured task closure before delivery.",
        )
        self.todo_session.mark_step(
            "deliver",
            "in_progress",
            "Continuing execution until the task emits an explicit completion signal.",
        )
        if self.session_state_active:
            await self.update_session_task(
                status="running",
                result_summary=summary,
                needs_confirmation=False,
                confirmation_deadline="",
            )
        await self.append_session_event(
            f"completion_signal_required:{self.task_id}:{origin}"
        )
        await self.update_task_inbox_status(
            status="running",
            event="completion_signal_required",
            detail=summary[:180],
            result={
                "completion_signal_required": {
                    "origin": origin,
                    "candidate_preview": summary,
                }
            },
        )
        goal_text = self.task_goal or "未提供"
        return {
            "continue_prompt": (
                "系统提示：当前处于 task 模式，不能把普通工具结果或普通文本直接当作最终交付。"
                "如果任务已经完成、失败、需要等待用户确认或等待外部条件，"
                "请调用 `complete_task` 发出结构化完成信号；否则继续执行下一步。"
                f"原始任务：{goal_text}。"
                f"当前结果摘要：{summary}。"
            )
        }

    async def handle(
        self, event: str, payload: Dict[str, Any]
    ) -> Dict[str, Any] | None:
        if event == "turn_start":
            turn = payload.get("turn")
            if self.session_state_active:
                await heartbeat_store.pulse(str(self.user_id), f"turn:{turn}")
            self.todo_session.heartbeat(f"turn:{turn}")
            task_manager.heartbeat(self.user_id, f"turn:{turn}")
            return None

        if event == "tool_call_started":
            self.flags.saw_tool_call = True
            tool_name = payload.get("name", "unknown")
            await self.append_session_event(
                f"tool_call_started:{self.task_id}:{tool_name}"
            )
            self.todo_session.mark_step(
                "act", "in_progress", f"Running `{tool_name}`..."
            )
            task_manager.heartbeat(self.user_id, f"tool:{tool_name}:running")
            return None

        if event == "tool_call_finished":
            return await self._handle_tool_call_finished(payload)

        if event == "retry_after_failure":
            failures = payload.get("failures")
            failure_list = failures if isinstance(failures, list) else []
            stage = max(
                1,
                min(self.max_recovery_attempts, self.flags.recovery_attempts or 1),
            )
            await self.append_session_event(
                f"retry_after_failure:{self.task_id}:attempt={stage}/{self.max_recovery_attempts}"
            )
            self.todo_session.mark_step(
                "act", "in_progress", "Retrying after tool failure."
            )
            task_manager.heartbeat(self.user_id, "retry_after_failure")
            return {
                "recovery_instruction": self.build_recovery_instruction(
                    stage,
                    self.max_recovery_attempts,
                    failure_list,
                )
            }

        if event == "final_response":
            return await self._handle_final_response(payload)

        if event == "max_turn_limit":
            await self._handle_max_turn_limit(payload)
            return None

        if event == "loop_guard":
            await self._handle_loop_guard(payload)
            return None

        return None

    async def _mark_waiting_external(
        self,
        *,
        final_text: str,
        detail: str,
        result: Dict[str, Any],
        output_text: str,
        followup: Dict[str, Any] | None = None,
    ) -> None:
        metadata: Dict[str, Any] = {}
        if isinstance(followup, dict) and followup:
            metadata["followup"] = dict(followup)
        if self.session_state_active:
            await self.update_session_task(
                status="waiting_external",
                result_summary=final_text,
                needs_confirmation=False,
                confirmation_deadline="",
            )
        await self.append_session_event(
            f"task_waiting_external:{self.task_id}:{detail[:120]}"
        )
        if self.task_inbox_id:
            await task_inbox.update_status(
                self.task_inbox_id,
                "waiting_external",
                event="waiting_external",
                detail=detail[:180],
                metadata=metadata,
                result=result,
                output={"text": output_text},
            )

    async def _mark_done(
        self,
        *,
        final_text: str,
        tool_name: str,
        terminal_ui: Dict[str, Any],
        terminal_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        if terminal_ui:
            self._append_pending_ui(terminal_ui)
        await self.update_session_task(
            status="done",
            result_summary=final_text,
            needs_confirmation=False,
            confirmation_deadline="",
            clear_active=True,
        )
        await self.append_session_event(f"terminal_tool_done:{self.task_id}:{tool_name}")
        if self.task_inbox_id:
            await task_inbox.complete(
                self.task_inbox_id,
                result={
                    "terminal_tool": str(tool_name),
                    "summary": final_text[:500],
                    "ui": terminal_ui,
                    "payload": terminal_payload,
                },
                final_output=final_text,
            )
        self.flags.completed = True
        return {"stop": True, "final_text": final_text}

    async def _mark_failed(
        self,
        *,
        final_text: str,
        tool_name: str,
        terminal_payload: Dict[str, Any] | None = None,
        failure_mode: str = "",
    ) -> Dict[str, Any] | None:
        if (
            failure_mode == "recoverable"
            and self.flags.recovery_attempts < self.max_recovery_attempts
        ):
            await self.append_session_event(
                (
                    f"recoverable_terminal_failure:{self.task_id}:{tool_name}:"
                    f"attempt={self.flags.recovery_attempts}/{self.max_recovery_attempts}"
                )
            )
            self.todo_session.mark_step(
                "verify",
                "in_progress",
                (
                    f"Recoverable failure detected. "
                    f"Auto-recovery attempt {self.flags.recovery_attempts}/{self.max_recovery_attempts}."
                ),
            )
            await self.update_session_task(
                status="running",
                result_summary=final_text,
                needs_confirmation=False,
                confirmation_deadline="",
            )
            return None

        await self.update_session_task(
            status="failed",
            result_summary=final_text,
            needs_confirmation=False,
            confirmation_deadline="",
            clear_active=True,
        )
        await self.append_session_event(f"terminal_tool_failed:{self.task_id}:{tool_name}")
        if self.task_inbox_id:
            await task_inbox.fail(
                self.task_inbox_id,
                error=final_text,
                result={
                    "terminal_tool": str(tool_name),
                    "summary": final_text[:500],
                    "payload": dict(terminal_payload or {}),
                },
            )
        self.flags.completed = True
        return {"stop": True, "final_text": final_text}

    async def _handle_tool_call_finished(
        self, payload: Dict[str, Any]
    ) -> Dict[str, Any] | None:
        tool_name = payload.get("name", "unknown")
        ok = bool(payload.get("ok"))
        summary = str(payload.get("summary", "")).strip()
        terminal = bool(payload.get("terminal"))
        task_outcome = str(payload.get("task_outcome") or "").strip().lower()
        terminal_text = str(payload.get("terminal_text") or "").strip()
        terminal_ui = payload.get("terminal_ui")
        terminal_payload = payload.get("terminal_payload")
        if not isinstance(terminal_ui, dict):
            terminal_ui = {}
        if not isinstance(terminal_payload, dict):
            terminal_payload = {}
        if terminal_ui and "ui" not in terminal_payload:
            terminal_payload["ui"] = terminal_ui
        payload_text = str(terminal_payload.get("text") or "").strip()
        if payload_text:
            if not terminal_text:
                terminal_text = payload_text
        failure_mode = (
            str(payload.get("failure_mode") or "").strip().lower() or "recoverable"
        )
        if failure_mode not in {"recoverable", "fatal"}:
            failure_mode = "recoverable"

        if ok:
            self.todo_session.mark_step(
                "act", "in_progress", f"`{tool_name}` ok: {summary[:120]}"
            )
        else:
            self.todo_session.mark_step(
                "act",
                "blocked",
                f"`{tool_name}` failed: {summary[:180]}",
            )
            self.todo_session.mark_step(
                "verify",
                "in_progress",
                "Detected failure; waiting for automatic retry.",
            )
            if (
                failure_mode == "recoverable"
                and self.flags.recovery_attempts < self.max_recovery_attempts
            ):
                self.flags.recovery_attempts += 1

        await self.append_session_event(
            (
                f"tool_call_finished:{self.task_id}:{tool_name}:"
                f"{'ok' if ok else 'failed'}:{failure_mode}:{summary[:160]}"
            )
        )
        task_manager.heartbeat(
            self.user_id, f"tool:{tool_name}:{'ok' if ok else 'failed'}"
        )

        if not terminal:
            return None

        final_text = (
            terminal_text
            or summary
            or ("✅ 工具执行完成。" if ok else f"❌ `{tool_name}` 执行失败。")
        )
        completion_signal = self._extract_completion_signal(payload)
        completion_status = str(
            completion_signal.get("status") or task_outcome or ""
        ).strip().lower()

        if ok and completion_status in {"partial", "waiting_user"}:
            deadline = (
                datetime.datetime.now().astimezone() + datetime.timedelta(seconds=180)
            ).isoformat(timespec="seconds")
            await self.update_session_task(
                status="waiting_user",
                result_summary=final_text,
                needs_confirmation=True,
                confirmation_deadline=deadline,
            )
            await self.append_session_event(
                f"task_waiting_user:{self.task_id}:{tool_name}"
            )
            self._append_pending_ui(
                {
                    "actions": [
                        [
                            {"text": "继续执行", "callback_data": "task_continue"},
                            {"text": "停止任务", "callback_data": "task_stop"},
                        ]
                    ]
                },
                replace=True,
            )
            if terminal_ui:
                self._append_pending_ui(terminal_ui)
            final_text = (
                f"{final_text}\n\n"
                "请确认下一步：点击按钮，或直接回复“继续”/“停止”（3分钟内有效）。"
            )
            await self.update_task_inbox_status(
                status="running",
                event="waiting_user_confirmation",
                detail=final_text[:180],
                result={
                    "task_outcome": "partial",
                    "summary": final_text[:500],
                },
            )
            self.flags.completed = True
            return {"stop": True, "final_text": final_text}
        elif ok:
            if completion_status == "waiting_external":
                followup = (
                    completion_signal.get("followup")
                    if isinstance(completion_signal.get("followup"), dict)
                    else None
                )
                await self._mark_waiting_external(
                    final_text=final_text,
                    detail=final_text,
                    result={
                        "terminal_tool": str(tool_name),
                        "summary": final_text[:500],
                        "payload": terminal_payload,
                    },
                    output_text=final_text,
                    followup=followup,
                )
                self.flags.completed = True
                return {"stop": True, "final_text": final_text}

            if completion_status in {"done", ""} and completion_signal.get("explicit"):
                return await self._mark_done(
                    final_text=final_text,
                    tool_name=tool_name,
                    terminal_ui=terminal_ui,
                    terminal_payload=terminal_payload,
                )

            if self._require_explicit_completion_signal():
                return await self._build_continue_after_missing_completion_signal(
                    candidate_text=final_text,
                    origin=f"terminal_tool:{tool_name}",
                )

            return await self._mark_done(
                final_text=final_text,
                tool_name=tool_name,
                terminal_ui=terminal_ui,
                terminal_payload=terminal_payload,
            )
        else:
            return await self._mark_failed(
                final_text=final_text,
                tool_name=tool_name,
                terminal_payload=terminal_payload,
                failure_mode=failure_mode,
            )

    async def _handle_final_response(
        self, payload: Dict[str, Any]
    ) -> Dict[str, Any] | None:
        source = str(payload.get("source") or "").strip().lower()
        if self.flags.completed and source in {
            "terminal_tool_short_circuit",
            "terminal_tool",
        }:
            return None
        preview = str(payload.get("text_preview", "")).strip()
        if self.ikaros_runtime:
            preview = self.sanitize_preview(preview)
        full_text = (
            str(payload.get("text") or payload.get("full_text") or "").strip() or preview
        )
        completion_signal = self._extract_completion_signal(payload)
        completion_status = str(completion_signal.get("status") or "").strip().lower()

        if completion_status in {"partial", "waiting_user"}:
            deadline = (
                datetime.datetime.now().astimezone() + datetime.timedelta(seconds=180)
            ).isoformat(timespec="seconds")
            await self.update_session_task(
                status="waiting_user",
                result_summary=full_text,
                needs_confirmation=True,
                confirmation_deadline=deadline,
            )
            await self.append_session_event(
                f"task_waiting_user:{self.task_id}:final_response"
            )
            self._append_pending_ui(
                {
                    "actions": [
                        [
                            {"text": "继续执行", "callback_data": "task_continue"},
                            {"text": "停止任务", "callback_data": "task_stop"},
                        ]
                    ]
                },
                replace=True,
            )
            final_text = (
                f"{full_text}\n\n"
                "请确认下一步：点击按钮，或直接回复“继续”/“停止”（3分钟内有效）。"
            )
            await self.update_task_inbox_status(
                status="running",
                event="waiting_user_confirmation",
                detail=final_text[:180],
                result={
                    "ikaros_mode": "final_response_partial",
                    "summary": final_text[:500],
                },
            )
            self.flags.completed = True
            return {"stop": True, "final_text": final_text}

        if completion_status == "waiting_external":
            followup = (
                completion_signal.get("followup")
                if isinstance(completion_signal.get("followup"), dict)
                else None
            )
            await self._mark_waiting_external(
                final_text=full_text,
                detail=preview or full_text,
                result={
                    "ikaros_mode": "final_response_waiting_external",
                    "summary": full_text[:500],
                },
                output_text=full_text,
                followup=followup,
            )
            task_manager.heartbeat(self.user_id, "final_response_waiting_external")
            self.flags.completed = True
            return None

        if completion_status in {"failed", "blocked", "cancelled", "timed_out", "error"}:
            self.todo_session.mark_failed("Model reported a blocked outcome.")
            if self.session_state_active:
                await self.update_session_task(
                    status="failed",
                    result_summary=full_text,
                    needs_confirmation=False,
                    confirmation_deadline="",
                    clear_active=True,
                )
                await self.append_session_event(
                    f"final_response_blocked:{self.task_id}:{preview[:120]}"
                )
            if self.task_inbox_id:
                await task_inbox.fail(
                    self.task_inbox_id,
                    error=full_text,
                    result={
                        "ikaros_mode": "final_response_blocked",
                        "summary": full_text[:500],
                    },
                )
            task_manager.heartbeat(self.user_id, "final_response_blocked")
            self.flags.completed = True
            return None

        if self._require_explicit_completion_signal() and not completion_signal.get("explicit"):
            return await self._build_continue_after_missing_completion_signal(
                candidate_text=full_text,
                origin=f"final_response:{source or 'model'}",
            )

        self.todo_session.mark_step("verify", "done", "Model produced final response.")
        self.todo_session.mark_step("deliver", "done", "Final response streaming.")

        auto_followup = self._maybe_pr_followup_metadata(preview)
        if auto_followup and self.task_inbox_id:
            await task_inbox.update_status(
                self.task_inbox_id,
                "waiting_external",
                event="auto_followup_waiting",
                detail=(auto_followup.get("detail") or preview)[:180],
                metadata={"followup": auto_followup["followup"]},
                result={
                    "ikaros_mode": "final_response",
                    "summary": full_text[:500],
                },
                output={"text": full_text},
            )
            if self.session_state_active:
                await self.update_session_task(
                    status="waiting_external",
                    result_summary=full_text,
                    needs_confirmation=False,
                    confirmation_deadline="",
                )

        if self.session_state_active:
            current = channel_runtime_store.get_active_task(
                platform_user_id=str(self.user_id),
            )
            if not current:
                current = await heartbeat_store.get_session_active_task(str(self.user_id))
            current_status = str((current or {}).get("status", "")).strip().lower()
            if current_status == "waiting_external":
                await self.update_session_task(
                    status="waiting_external",
                    result_summary=full_text,
                    needs_confirmation=False,
                    confirmation_deadline="",
                )
            elif current_status not in {
                "waiting_user",
                "waiting_external",
                "failed",
                "cancelled",
                "timed_out",
            }:
                await self.update_session_task(
                    status="done",
                    result_summary=full_text,
                    needs_confirmation=False,
                    confirmation_deadline="",
                    clear_active=True,
                )
            await self.append_session_event(
                f"final_response:{self.task_id}:{preview[:120]}"
            )

        if self.task_inbox_id:
            current_task = await task_inbox.get(self.task_inbox_id)
            current_task_status = (
                str((current_task or {}).status if current_task else "").strip().lower()
            )
            if current_task_status == "waiting_external":
                await task_inbox.update_status(
                    self.task_inbox_id,
                    "waiting_external",
                    event="final_response_kept_open",
                    detail=preview[:180],
                    result={
                        "ikaros_mode": "final_response",
                        "summary": full_text[:500],
                    },
                    output={"text": full_text},
                )
            else:
                await task_inbox.complete(
                    self.task_inbox_id,
                    result={
                        "ikaros_mode": "final_response",
                        "summary": full_text[:500],
                    },
                    final_output=full_text,
                )

        task_manager.heartbeat(self.user_id, "final_response")
        self.flags.completed = True
        return None

    @staticmethod
    def _maybe_pr_followup_metadata(preview: str) -> Dict[str, Any] | None:
        text = str(preview or "").strip()
        if not text:
            return None
        match = re.search(r"https://github\.com/[^\s)]+/pull/\d+", text)
        if match is None:
            return None
        pr_url = match.group(0)
        return {
            "detail": f"waiting for pull request merge: {pr_url}",
            "followup": {
                "done_when": "GitHub pull request merged",
                "refs": {"pr_url": pr_url},
                "announce_before_action": True,
            },
        }

    async def _handle_max_turn_limit(self, payload: Dict[str, Any]) -> None:
        terminal_preview = str(payload.get("terminal_text_preview") or "").strip()
        terminal_summary = str(payload.get("terminal_summary") or "").strip()
        summary = (terminal_preview or terminal_summary)[:500]
        detail = "Reached max tool-loop turns before completion."
        if summary:
            detail = f"{detail} Last visible intermediate result: {summary}"

        self.todo_session.mark_failed("Reached max tool-loop turns before completion.")
        if self.session_state_active:
            await self.update_session_task(
                status="failed",
                result_summary=detail,
                needs_confirmation=False,
                confirmation_deadline="",
                clear_active=True,
            )
            await self.append_session_event(f"max_turn_limit:{self.task_id}")
        task_manager.heartbeat(self.user_id, "max_turn_limit")
        self.flags.blocked = True
        self.flags.blocked_reason = "max_turn_limit"
        await self.update_task_inbox_status(
            status="failed",
            event="max_turn_limit",
            detail=detail[:180],
            result={
                "error": "max_turn_limit",
                "last_terminal_summary": summary,
            },
            final_output=summary,
        )

    async def _handle_loop_guard(self, payload: Dict[str, Any]) -> None:
        repeat_details = str(payload.get("repeat_details") or "").strip()
        if self.session_state_active:
            await self.update_session_task(
                status="failed",
                result_summary="Loop guard triggered due to repeated tool calls.",
                needs_confirmation=False,
                confirmation_deadline="",
                clear_active=True,
            )
            await self.append_session_event(
                f"loop_guard:{self.task_id}:{payload.get('repeat_count')}"
            )
        self.flags.blocked = True
        self.flags.blocked_reason = "loop_guard"
        await self.update_task_inbox_status(
            status="failed",
            event="loop_guard",
            detail=repeat_details[:180]
            or "Loop guard triggered due to repeated tool calls.",
            result={
                "error": "loop_guard",
                "repeat_count": int(payload.get("repeat_count") or 0),
                "repeated_calls": payload.get("repeated_calls") or [],
            },
            final_output="",
        )
