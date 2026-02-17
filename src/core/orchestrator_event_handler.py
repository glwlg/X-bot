from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

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


class OrchestratorEventHandler:
    """Handle ai_service event callbacks for one orchestrator loop."""

    def __init__(
        self,
        *,
        user_id: Any,
        task_id: str,
        task_inbox_id: str,
        ctx: Any,
        todo_session: Any,
        manager_runtime: bool,
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
        self.ctx = ctx
        self.todo_session = todo_session
        self.manager_runtime = bool(manager_runtime)
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
            await self._handle_final_response(payload)
            return None

        if event == "max_turn_limit":
            await self._handle_max_turn_limit(payload)
            return None

        if event == "loop_guard":
            await self._handle_loop_guard(payload)
            return None

        return None

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

        if ok and task_outcome == "partial":
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
        elif ok:
            if terminal_ui:
                self._append_pending_ui(terminal_ui)
            await self.update_session_task(
                status="done",
                result_summary=final_text,
                needs_confirmation=False,
                confirmation_deadline="",
                clear_active=True,
            )
            await self.append_session_event(
                f"terminal_tool_done:{self.task_id}:{tool_name}"
            )
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
        else:
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
                    result_summary=summary,
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
            await self.append_session_event(
                f"terminal_tool_failed:{self.task_id}:{tool_name}"
            )
            if self.task_inbox_id:
                await task_inbox.fail(
                    self.task_inbox_id,
                    error=final_text,
                    result={
                        "terminal_tool": str(tool_name),
                        "summary": final_text[:500],
                    },
                )

        self.flags.completed = True
        return {"stop": True, "final_text": final_text}

    async def _handle_final_response(self, payload: Dict[str, Any]) -> None:
        if self.flags.completed and str(
            payload.get("source") or ""
        ).strip().lower() in {
            "terminal_tool_short_circuit",
            "terminal_tool",
        }:
            return
        self.todo_session.mark_step("verify", "done", "Model produced final response.")
        self.todo_session.mark_step("deliver", "done", "Final response streaming.")
        preview = str(payload.get("text_preview", "")).strip()
        if self.manager_runtime:
            preview = self.sanitize_preview(preview)

        if self.session_state_active:
            current = await heartbeat_store.get_session_active_task(str(self.user_id))
            current_status = str((current or {}).get("status", "")).strip().lower()
            if current_status not in {
                "waiting_user",
                "failed",
                "cancelled",
                "timed_out",
            }:
                await self.update_session_task(
                    status="done",
                    result_summary=preview,
                    needs_confirmation=False,
                    confirmation_deadline="",
                    clear_active=True,
                )
            await self.append_session_event(
                f"final_response:{self.task_id}:{preview[:120]}"
            )

        if self.task_inbox_id:
            await task_inbox.complete(
                self.task_inbox_id,
                result={
                    "manager_mode": "final_response",
                    "summary": preview[:500],
                },
                final_output=preview,
            )

        task_manager.heartbeat(self.user_id, "final_response")
        self.flags.completed = True

    async def _handle_max_turn_limit(self, payload: Dict[str, Any]) -> None:
        terminal_preview = str(payload.get("terminal_text_preview") or "").strip()
        terminal_summary = str(payload.get("terminal_summary") or "").strip()
        if self.session_state_active and (terminal_preview or terminal_summary):
            summary = (terminal_preview or terminal_summary)[:500]
            await self.update_session_task(
                status="done",
                result_summary=summary,
                needs_confirmation=False,
                confirmation_deadline="",
                clear_active=True,
            )
            await self.append_session_event(
                f"max_turn_but_completed:{self.task_id}:{summary[:120]}"
            )
            if self.task_inbox_id:
                await task_inbox.complete(
                    self.task_inbox_id,
                    result={
                        "manager_mode": "max_turn_terminal",
                        "summary": summary,
                    },
                    final_output=summary,
                )
            self.flags.completed = True
            return

        self.todo_session.mark_failed("Reached max tool-loop turns before completion.")
        if self.session_state_active:
            await self.update_session_task(
                status="failed",
                result_summary="Reached max tool-loop turns before completion.",
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
            detail="Reached max tool-loop turns before completion.",
            result={"error": "max_turn_limit"},
            final_output="",
        )

    async def _handle_loop_guard(self, payload: Dict[str, Any]) -> None:
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
            detail="Loop guard triggered due to repeated tool calls.",
            result={"error": "loop_guard"},
            final_output="",
        )
