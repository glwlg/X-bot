from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, Iterable
from uuid import uuid4

from core.background_delivery import push_background_text
from core.file_artifacts import extract_saved_file_rows, merge_file_rows, normalize_file_rows
from core.heartbeat_store import heartbeat_store
from core.platform.models import Chat, MessageType, UnifiedContext, UnifiedMessage, User
from core.platform.registry import adapter_manager
from core.runtime_callbacks import pop_runtime_callback, set_runtime_callback
from core.subagent_types import SubagentResult
from core.task_inbox import task_inbox

logger = logging.getLogger(__name__)

_BLOCKING_EVENTS = {
    "max_turn_limit",
    "loop_guard",
    "semantic_loop_guard",
    "tool_budget_guard",
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_text(value: Any, *, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _normalize_tokens(values: Iterable[Any] | None) -> list[str]:
    rows: list[str] = []
    for item in list(values or []):
        token = str(item or "").strip()
        if token and token not in rows:
            rows.append(token)
    return rows


def _coerce_stream(raw_stream: Any) -> AsyncIterator[str]:
    if hasattr(raw_stream, "__aiter__"):
        return raw_stream
    if inspect.isawaitable(raw_stream):

        async def _single() -> AsyncIterator[str]:
            result = await raw_stream
            if result is not None:
                yield str(result)

        return _single()
    raise TypeError("agent_orchestrator.handle_message must return an async iterator")


async def _collect_chunks(stream: AsyncIterator[str]) -> list[str]:
    chunks: list[str] = []
    async for chunk in stream:
        text = str(chunk or "").strip()
        if text:
            chunks.append(text)
    return chunks


class _SubagentSilentAdapter:
    async def reply_text(self, ctx, text: str, ui=None, **kwargs):
        _ = (ctx, text, ui, kwargs)
        return SimpleNamespace(id=f"subagent-silent-{int(datetime.now().timestamp())}")

    async def edit_text(self, ctx, message_id: str, text: str, **kwargs):
        _ = (ctx, text, kwargs)
        return SimpleNamespace(id=message_id)

    async def reply_document(
        self, ctx, document, filename=None, caption=None, **kwargs
    ):
        _ = (ctx, document, filename, caption, kwargs)
        return SimpleNamespace(id=filename or "doc")

    async def reply_photo(self, ctx, photo, caption=None, **kwargs):
        _ = (ctx, photo, caption, kwargs)
        return SimpleNamespace(id="photo")

    async def reply_video(self, ctx, video, caption=None, **kwargs):
        _ = (ctx, video, caption, kwargs)
        return SimpleNamespace(id="video")

    async def reply_audio(self, ctx, audio, caption=None, **kwargs):
        _ = (ctx, audio, caption, kwargs)
        return SimpleNamespace(id="audio")

    async def delete_message(self, ctx, message_id: str, chat_id=None, **kwargs):
        _ = (ctx, message_id, chat_id, kwargs)
        return True

    async def send_chat_action(self, ctx, action: str, chat_id=None, **kwargs):
        _ = (ctx, action, chat_id, kwargs)
        return True

    async def download_file(self, ctx, file_id: str, **kwargs) -> bytes:
        _ = (ctx, file_id, kwargs)
        raise RuntimeError("subagent context does not support file download")


@dataclass
class _SubagentRun:
    subagent_id: str
    goal: str
    user_id: str
    chat_id: str
    platform: str
    allowed_tools: list[str]
    allowed_skills: list[str]
    mode: str
    timeout_sec: int
    parent_task_id: str = ""
    parent_task_inbox_id: str = ""
    notify_platform: str = ""
    notify_chat_id: str = ""
    detached_task_id: str = ""
    task_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    result: SubagentResult | None = None
    task: asyncio.Task | None = None


class SubagentSupervisor:
    def __init__(self) -> None:
        self._runs: dict[str, _SubagentRun] = {}
        self._lock = asyncio.Lock()
        self.await_timeout_sec = max(
            1.0, float(os.getenv("SUBAGENT_AWAIT_TIMEOUT_SEC", "45"))
        )

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        async with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            task = run.task
            if task is None or task.done():
                continue
            task.cancel()
        for run in runs:
            task = run.task
            if task is None:
                continue
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def spawn(
        self,
        *,
        ctx: Any | None,
        goal: str,
        allowed_tools: Iterable[Any],
        allowed_skills: Iterable[Any] | None = None,
        mode: str = "inline",
        timeout_sec: int = 300,
        parent_task_id: str = "",
        parent_task_inbox_id: str = "",
        user_id_override: str = "",
        platform_override: str = "",
        chat_id_override: str = "",
        task_metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        safe_goal = _safe_text(goal, limit=12000)
        safe_tools = _normalize_tokens(allowed_tools)
        safe_skills = _normalize_tokens(allowed_skills)
        safe_mode = str(mode or "inline").strip().lower() or "inline"
        if safe_mode not in {"inline", "detached"}:
            safe_mode = "inline"
        safe_timeout = max(30, int(timeout_sec or 300))
        if not safe_goal:
            return {
                "ok": False,
                "error_code": "invalid_args",
                "message": "goal is required",
                "failure_mode": "recoverable",
            }
        if not safe_tools:
            return {
                "ok": False,
                "error_code": "invalid_args",
                "message": "allowed_tools is required",
                "failure_mode": "recoverable",
            }

        msg = getattr(ctx, "message", None)
        msg_user = getattr(msg, "user", None)
        msg_chat = getattr(msg, "chat", None)
        platform = (
            str(platform_override or getattr(msg, "platform", "") or "")
            .strip()
            .lower()
        )
        chat_id = str(chat_id_override or getattr(msg_chat, "id", "") or "").strip()
        user_id = str(user_id_override or getattr(msg_user, "id", "") or "").strip()
        bound_session_id = ""
        if user_id and (not platform or not chat_id):
            with contextlib.suppress(Exception):
                target = await heartbeat_store.get_delivery_target(user_id)
                platform = platform or str(target.get("platform") or "").strip().lower()
                chat_id = chat_id or str(target.get("chat_id") or "").strip()
                bound_session_id = str(target.get("session_id") or "").strip()
        elif user_id:
            with contextlib.suppress(Exception):
                target = await heartbeat_store.get_delivery_target(user_id)
                bound_session_id = str(target.get("session_id") or "").strip()

        task_metadata_payload = dict(task_metadata or {})
        if bound_session_id and not str(task_metadata_payload.get("session_id") or "").strip():
            task_metadata_payload["session_id"] = bound_session_id

        subagent_id = f"subagent-{uuid4().hex[:10]}"
        run = _SubagentRun(
            subagent_id=subagent_id,
            goal=safe_goal,
            user_id=user_id,
            chat_id=chat_id,
            platform=platform,
            allowed_tools=safe_tools,
            allowed_skills=safe_skills,
            mode=safe_mode,
            timeout_sec=safe_timeout,
            parent_task_id=str(parent_task_id or "").strip(),
            parent_task_inbox_id=str(parent_task_inbox_id or "").strip(),
            notify_platform=platform,
            notify_chat_id=chat_id,
            task_metadata=task_metadata_payload,
        )

        if safe_mode == "detached":
            detached_metadata = {
                "executor_type": "subagent",
                "subagent_ids": [subagent_id],
                "tool_scope": {
                    "allowed_tools": list(safe_tools),
                    "allowed_skills": list(safe_skills),
                },
                "notify_platform": run.notify_platform,
                "notify_chat_id": run.notify_chat_id,
                "parent_task_id": run.parent_task_id,
                "parent_task_inbox_id": run.parent_task_inbox_id,
            }
            if run.task_metadata:
                detached_metadata.update(dict(run.task_metadata))
            detached_task = await task_inbox.submit(
                source="subagent",
                goal=safe_goal,
                user_id=user_id or "system",
                requires_reply=True,
                metadata=detached_metadata,
            )
            run.detached_task_id = detached_task.task_id
            await task_inbox.update_status(
                detached_task.task_id,
                "running",
                event="subagent_started",
                detail=safe_goal[:180],
            )

        run.task = asyncio.create_task(
            self._run_subagent(run),
            name=f"subagent-{subagent_id}",
        )
        async with self._lock:
            self._runs[subagent_id] = run

        summary = (
            f"已启动后台子任务 `{run.detached_task_id or subagent_id}`。"
            if safe_mode == "detached"
            else f"已启动子任务 `{subagent_id}`。"
        )
        return {
            "ok": True,
            "subagent_id": subagent_id,
            "task_id": run.detached_task_id,
            "terminal": False,
            "task_outcome": "partial" if safe_mode == "detached" else "running",
            "summary": summary,
            "text": summary,
            "async_dispatch": safe_mode == "detached",
            "executor_name": subagent_id,
            "payload": {
                "subagent_id": subagent_id,
                "task_id": run.detached_task_id,
                "mode": safe_mode,
                "allowed_tools": list(safe_tools),
                "allowed_skills": list(safe_skills),
            },
        }

    async def await_subagents(
        self,
        *,
        subagent_ids: Iterable[Any],
        wait_policy: str = "all",
    ) -> Dict[str, Any]:
        safe_ids = _normalize_tokens(subagent_ids)
        if not safe_ids:
            return {
                "ok": False,
                "error_code": "invalid_args",
                "message": "subagent_ids is required",
                "failure_mode": "recoverable",
            }

        safe_wait_policy = str(wait_policy or "all").strip().lower() or "all"
        if safe_wait_policy not in {"all", "any", "none"}:
            safe_wait_policy = "all"

        async with self._lock:
            runs = [self._runs.get(item) for item in safe_ids]

        existing_runs = [run for run in runs if run is not None]
        missing_ids = [safe_ids[idx] for idx, run in enumerate(runs) if run is None]

        pending_tasks = [
            run.task for run in existing_runs if run and run.result is None and run.task
        ]
        if pending_tasks and safe_wait_policy != "none":
            if safe_wait_policy == "all":
                await asyncio.wait(
                    pending_tasks,
                    timeout=self.await_timeout_sec,
                    return_when=asyncio.ALL_COMPLETED,
                )
            else:
                await asyncio.wait(
                    pending_tasks,
                    timeout=self.await_timeout_sec,
                    return_when=asyncio.FIRST_COMPLETED,
                )

        results = [
            run.result.to_dict()
            for run in existing_runs
            if run is not None and run.result is not None
        ]
        success_count = sum(
            1
            for item in results
            if isinstance(item, dict) and bool(item.get("ok"))
        )
        failure_count = max(0, len(results) - success_count)
        pending = [
            run.subagent_id
            for run in existing_runs
            if run is not None and run.result is None
        ]
        if pending:
            summary = (
                f"{len(results)} 个子任务已返回结果，"
                f"其中 {success_count} 个成功、{failure_count} 个失败，"
                f"{len(pending)} 个仍在运行。"
            )
        else:
            summary = (
                f"{len(results)} 个子任务已返回结果，"
                f"其中 {success_count} 个成功、{failure_count} 个失败。"
            )
        return {
            "ok": True,
            "terminal": False,
            "task_outcome": "partial" if pending else "collected",
            "summary": summary,
            "text": summary,
            "results": results,
            "pending": pending,
            "missing": missing_ids,
            "payload": {
                "results": results,
                "pending": pending,
                "missing": missing_ids,
                "all_completed": not pending,
                "success_count": success_count,
                "failure_count": failure_count,
            },
        }

    async def cancel_for_user(
        self,
        *,
        user_id: str,
        reason: str = "cancelled_by_user",
    ) -> Dict[str, Any]:
        safe_user_id = str(user_id or "").strip()
        async with self._lock:
            runs = [run for run in self._runs.values() if run.user_id == safe_user_id]
        cancelled = 0
        detached_task_ids: list[str] = []
        for run in runs:
            if run.result is not None:
                continue
            task = run.task
            if task is None or task.done():
                continue
            task.cancel()
            cancelled += 1
            if run.detached_task_id:
                detached_task_ids.append(run.detached_task_id)
                with contextlib.suppress(Exception):
                    await task_inbox.update_status(
                        run.detached_task_id,
                        "cancelled",
                        event="subagent_cancelled",
                        detail=reason[:180],
                        result={
                            "summary": "subagent cancelled",
                            "error": reason[:500],
                        },
                        output={"text": "任务已取消。"},
                    )
        return {
            "cancelled": cancelled,
            "task_ids": detached_task_ids,
            "summary": f"cancelled {cancelled} subagent task(s)",
        }

    def _build_context(self, run: _SubagentRun) -> UnifiedContext:
        now = datetime.now()
        user = User(
            id=run.user_id or "subagent-user",
            username=f"subagent_{run.subagent_id}",
            first_name="Subagent",
            last_name="Runner",
        )
        chat = Chat(
            id=run.chat_id or run.user_id or run.subagent_id,
            type="private",
            title=f"subagent-{run.subagent_id}",
        )
        message = UnifiedMessage(
            id=f"subagent-msg-{run.subagent_id}",
            platform="subagent_kernel",
            user=user,
            chat=chat,
            date=now,
            type=MessageType.TEXT,
            text=run.goal,
        )
        ctx = UnifiedContext(
            message=message,
            platform_ctx=None,
            platform_event=None,
            _adapter=_SubagentSilentAdapter(),
            user=user,
        )
        ctx.user_data["runtime_user_id"] = (
            f"subagent::{run.subagent_id}::{run.user_id or 'anonymous'}"
        )
        ctx.user_data["runtime_agent_kind"] = "subagent"
        ctx.user_data["subagent_session_state_enabled"] = False
        ctx.user_data["task_goal"] = run.goal
        ctx.user_data["allowed_tool_names"] = list(run.allowed_tools)
        ctx.user_data["allowed_skill_names"] = list(run.allowed_skills)
        return ctx

    async def _run_subagent(self, run: _SubagentRun) -> SubagentResult:
        latest_terminal_payload: Dict[str, Any] = {}
        latest_terminal_text = ""
        latest_terminal_summary = ""
        latest_terminal_ok = False
        latest_terminal_failure_mode = ""
        latest_error_text = ""
        latest_closure_reason = ""
        ctx = self._build_context(run)

        async def _progress_callback(snapshot: Dict[str, Any]) -> None:
            nonlocal latest_terminal_payload, latest_terminal_text, latest_terminal_summary
            nonlocal latest_terminal_ok, latest_terminal_failure_mode
            nonlocal latest_error_text, latest_closure_reason
            event_name = str(snapshot.get("event") or "").strip().lower()
            if event_name in _BLOCKING_EVENTS:
                latest_closure_reason = event_name
            summary = str(snapshot.get("summary") or "").strip()
            if summary and bool(snapshot.get("ok")) is False:
                latest_error_text = summary
            if event_name != "tool_call_finished":
                return
            if not bool(snapshot.get("terminal")):
                return
            terminal_payload = snapshot.get("terminal_payload")
            if isinstance(terminal_payload, dict) and terminal_payload:
                latest_terminal_payload = dict(terminal_payload)
            latest_terminal_text = str(snapshot.get("terminal_text") or "").strip()
            latest_terminal_summary = str(snapshot.get("summary") or "").strip()
            latest_terminal_ok = bool(snapshot.get("ok"))
            latest_terminal_failure_mode = str(
                snapshot.get("failure_mode") or "recoverable"
            ).strip()
            if not latest_terminal_ok:
                latest_error_text = latest_terminal_text or latest_terminal_summary

        set_runtime_callback(ctx, "subagent_progress_callback", _progress_callback)
        message_history = [{"role": "user", "parts": [{"text": run.goal}]}]
        try:
            from core.agent_orchestrator import agent_orchestrator

            raw_stream = agent_orchestrator.handle_message(ctx, message_history)
            stream = _coerce_stream(raw_stream)
            chunks = await asyncio.wait_for(
                _collect_chunks(stream),
                timeout=float(run.timeout_sec),
            )
            final_text = "\n".join(chunks).strip()
            payload = dict(latest_terminal_payload or {})
            payload_text = _safe_text(payload.get("text"), limit=12000)
            if not final_text:
                final_text = payload_text or latest_terminal_text
            if not final_text:
                final_text = "subagent finished with no text output"

            files = merge_file_rows(
                normalize_file_rows(payload.get("files")),
                extract_saved_file_rows(final_text),
            )
            summary_text = (
                _safe_text(payload_text, limit=500)
                or _safe_text(latest_terminal_summary, limit=500)
                or _safe_text(final_text, limit=500)
            )
            if latest_terminal_text and not latest_terminal_ok:
                message = (
                    latest_terminal_text
                    or latest_terminal_summary
                    or latest_error_text
                    or final_text
                )
                result = SubagentResult(
                    subagent_id=run.subagent_id,
                    ok=False,
                    summary=_safe_text(message, limit=500),
                    text=_safe_text(message, limit=12000),
                    error=_safe_text(message, limit=12000),
                    files=files,
                    diagnostic_summary=_safe_text(message, limit=12000),
                    task_outcome="blocked",
                    failure_mode=latest_terminal_failure_mode or "recoverable",
                    ikaros_followup_required=True,
                )
            else:
                result = SubagentResult(
                    subagent_id=run.subagent_id,
                    ok=True,
                    summary=summary_text,
                    text=_safe_text(final_text, limit=12000),
                    files=files,
                    diagnostic_summary=summary_text,
                    task_outcome="done",
                    failure_mode="",
                    ikaros_followup_required=False,
                )
        except asyncio.CancelledError:
            result = SubagentResult(
                subagent_id=run.subagent_id,
                ok=False,
                summary="subagent cancelled",
                text="subagent cancelled",
                error="subagent cancelled",
                diagnostic_summary="subagent cancelled",
                task_outcome="cancelled",
                failure_mode="recoverable",
                ikaros_followup_required=False,
            )
        except asyncio.TimeoutError:
            message = f"subagent timeout after {int(run.timeout_sec)}s"
            result = SubagentResult(
                subagent_id=run.subagent_id,
                ok=False,
                summary=message,
                text=message,
                error=message,
                diagnostic_summary=message,
                task_outcome="blocked",
                failure_mode="recoverable",
                ikaros_followup_required=True,
            )
        except Exception as exc:
            message = f"subagent failed: {exc}"
            logger.error("Subagent run failed id=%s err=%s", run.subagent_id, exc, exc_info=True)
            result = SubagentResult(
                subagent_id=run.subagent_id,
                ok=False,
                summary=_safe_text(message, limit=500),
                text=_safe_text(message, limit=12000),
                error=_safe_text(message, limit=12000),
                diagnostic_summary=_safe_text(
                    latest_error_text or latest_closure_reason or message,
                    limit=12000,
                ),
                task_outcome="blocked",
                failure_mode="recoverable",
                ikaros_followup_required=True,
            )
        finally:
            pop_runtime_callback(ctx, "subagent_progress_callback")

        run.result = result
        if run.mode == "detached":
            await self._handle_detached_result(run, result)
        return result

    @staticmethod
    def _as_attempt_result(result: SubagentResult) -> Dict[str, Any]:
        return {
            "ok": bool(result.ok),
            "summary": _safe_text(result.summary, limit=1000),
            "error": _safe_text(result.error, limit=1000),
            "payload": {
                "text": _safe_text(result.text, limit=12000),
                "files": normalize_file_rows(result.files),
                "diagnostic_summary": _safe_text(result.diagnostic_summary, limit=12000),
                "attempt_outcome": _safe_text(
                    result.task_outcome or ("done" if result.ok else "blocked"),
                    limit=40,
                ).lower(),
                "failure_mode": _safe_text(result.failure_mode, limit=40).lower(),
                "closure_reason": (
                    "subagent_failed"
                    if result.ikaros_followup_required and not result.ok
                    else ""
                ),
            },
        }

    async def _resolve_delivery_artifacts(
        self,
        *,
        run: _SubagentRun,
        result: SubagentResult,
    ) -> tuple[str, list[dict[str, str]]]:
        if not bool(run.task_metadata.get("staged_session")):
            return self._build_delivery_text(run, result), normalize_file_rows(result.files)

        try:
            from ikaros.relay.closure_service import ikaros_closure_service
            from shared.contracts.dispatch import TaskEnvelope as DispatchTaskEnvelope

            attempt_task = DispatchTaskEnvelope(
                task_id=run.detached_task_id or run.subagent_id,
                executor_id=run.subagent_id,
                instruction=run.goal,
                source="subagent",
                metadata=dict(run.task_metadata or {}),
            )
            decision = await ikaros_closure_service.resolve_attempt(
                task=attempt_task,
                result=self._as_attempt_result(result),
                platform=run.notify_platform,
                chat_id=run.notify_chat_id,
            )
        except Exception:
            logger.error(
                "Failed to resolve staged subagent delivery id=%s",
                run.subagent_id,
                exc_info=True,
            )
            return self._build_delivery_text(run, result), normalize_file_rows(result.files)

        kind = str(decision.get("kind") or "").strip().lower()
        if kind == "final":
            final_result = dict(decision.get("result") or {})
            payload = final_result.get("payload")
            payload_obj = dict(payload) if isinstance(payload, dict) else {}
            text = _safe_text(
                payload_obj.get("text")
                or final_result.get("text")
                or final_result.get("summary")
                or result.text
            )
            files = normalize_file_rows(payload_obj.get("files") or final_result.get("files"))
            return text or self._build_delivery_text(run, result), files
        if kind in {"waiting_user", "next_stage"}:
            return _safe_text(decision.get("text"), limit=12000), normalize_file_rows(
                decision.get("files") or []
            )
        return self._build_delivery_text(run, result), normalize_file_rows(result.files)

    async def _handle_detached_result(
        self,
        run: _SubagentRun,
        result: SubagentResult,
    ) -> None:
        task_id = str(run.detached_task_id or "").strip()
        result_dict = result.to_dict()
        delivery_text, delivery_files = await self._resolve_delivery_artifacts(
            run=run,
            result=result,
        )
        if task_id:
            metadata = {
                "executor_type": "subagent",
                "subagent_ids": [run.subagent_id],
                "tool_scope": {
                    "allowed_tools": list(run.allowed_tools),
                    "allowed_skills": list(run.allowed_skills),
                },
                "notify_platform": run.notify_platform,
                "notify_chat_id": run.notify_chat_id,
                "delivery_state": "pending",
            }
            if run.task_metadata:
                metadata.update(dict(run.task_metadata))
            if result.ok and not result.ikaros_followup_required:
                await task_inbox.complete(
                    task_id,
                    result={"payload": result_dict, "summary": result.summary},
                    final_output=delivery_text,
                    output={"text": delivery_text, "files": delivery_files},
                )
            else:
                await task_inbox.fail(
                    task_id,
                    error=result.error or result.summary or "subagent failed",
                    result={"payload": result_dict, "summary": result.summary},
                    output={"text": delivery_text, "files": delivery_files},
                )
            await task_inbox.update_status(
                task_id,
                "completed" if result.ok and not result.ikaros_followup_required else "failed",
                event="subagent_finished",
                detail=result.summary[:180],
                metadata=metadata,
            )

        delivered = False
        if run.notify_platform and run.notify_chat_id:
            delivered = await push_background_text(
                platform=run.notify_platform,
                chat_id=run.notify_chat_id,
                text=delivery_text,
                filename_prefix="subagent",
                record_history=bool(str(run.user_id or "").strip()),
                history_user_id=run.user_id,
                history_session_id=str(
                    (run.task_metadata or {}).get("session_id") or ""
                ).strip(),
            )
            if delivery_files:
                delivered = (
                    await self._deliver_files(
                        platform=run.notify_platform,
                        chat_id=run.notify_chat_id,
                        files=delivery_files,
                    )
                    or delivered
                )

        if task_id:
            await task_inbox.update_status(
                task_id,
                "completed" if result.ok and not result.ikaros_followup_required else "failed",
                event="delivery_finished" if delivered else "delivery_skipped",
                detail=("background delivery sent" if delivered else "background delivery unavailable"),
                metadata={"delivery_state": "delivered" if delivered else "skipped"},
            )

    @staticmethod
    def _build_delivery_text(run: _SubagentRun, result: SubagentResult) -> str:
        if result.ok and not result.ikaros_followup_required:
            body = _safe_text(result.text or result.summary, limit=12000)
            return (
                f"后台任务 `{run.detached_task_id or run.subagent_id}` 已完成。\n\n{body}"
            ).strip()
        detail = _safe_text(
            result.diagnostic_summary or result.error or result.text or result.summary,
            limit=12000,
        )
        return (
            f"后台任务 `{run.detached_task_id or run.subagent_id}` 未完成。\n\n{detail}"
        ).strip()

    async def _deliver_files(
        self,
        *,
        platform: str,
        chat_id: str,
        files: list[dict[str, str]],
    ) -> bool:
        safe_files = normalize_file_rows(files)
        if not safe_files:
            return False
        try:
            adapter = adapter_manager.get_adapter(platform)
        except Exception:
            return False

        delivered = False
        for item in safe_files:
            path_text = str(item.get("path") or "").strip()
            if not path_text:
                continue
            path_obj = Path(path_text).expanduser().resolve()
            if not path_obj.exists() or not path_obj.is_file():
                continue
            kind = str(item.get("kind") or "document").strip().lower() or "document"
            caption = str(item.get("caption") or "").strip() or None
            filename = str(item.get("filename") or path_obj.name).strip() or path_obj.name
            sender = None
            kwargs: Dict[str, Any] = {"chat_id": chat_id}
            if kind == "photo":
                sender = getattr(adapter, "send_photo", None)
                kwargs["photo"] = str(path_obj)
            elif kind == "video":
                sender = getattr(adapter, "send_video", None)
                kwargs["video"] = str(path_obj)
            elif kind == "audio":
                sender = getattr(adapter, "send_audio", None)
                kwargs["audio"] = str(path_obj)
            if not callable(sender):
                sender = getattr(adapter, "send_document", None)
                kwargs = {
                    "chat_id": chat_id,
                    "document": str(path_obj),
                    "filename": filename,
                }
            if not callable(sender):
                continue
            if caption:
                kwargs["caption"] = caption
            result_obj = sender(**kwargs)
            if inspect.isawaitable(result_obj):
                await result_obj
            delivered = True
        return delivered


subagent_supervisor = SubagentSupervisor()
