from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from core.file_artifacts import extract_saved_file_rows, merge_file_rows, normalize_file_rows
from core.heartbeat_store import heartbeat_store
from core.task_cards import format_stage_continue_card, format_waiting_user_card
from core.task_inbox import task_inbox
from manager.dispatch.service import manager_dispatch_service
from manager.planning.stage_planner import (
    add_adjustment,
    build_stage_instruction,
    count_adjustments,
    get_current_stage,
    get_stage_position,
    mark_stage_blocked,
    mark_stage_completed,
    merge_collected_files,
    normalize_stage_plan,
)
from shared.contracts.dispatch import TaskEnvelope

logger = logging.getLogger(__name__)

_CONTINUE_CUES = {"继续", "继续执行", "继续重部署", "resume", "continue"}
_STOP_CUES = {"停止", "取消", "停止任务", "stop", "cancel"}


def _safe_text(value: Any, *, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _future_iso(seconds: int) -> str:
    return (datetime.now().astimezone() + timedelta(seconds=max(0, int(seconds or 0)))).isoformat(
        timespec="seconds"
    )


def _session_task_id(metadata: Dict[str, Any]) -> str:
    return (
        _safe_text(metadata.get("session_task_id"), limit=80)
        or _safe_text(metadata.get("task_inbox_id"), limit=80)
        or _safe_text(metadata.get("user_visible_task_id"), limit=80)
    )


def _task_inbox_id(metadata: Dict[str, Any]) -> str:
    return (
        _safe_text(metadata.get("task_inbox_id"), limit=80)
        or _safe_text(metadata.get("session_task_id"), limit=80)
    )


def _is_staged_session(metadata: Dict[str, Any]) -> bool:
    if str(metadata.get("staged_session") or "").strip().lower() == "true":
        return True
    return bool(_session_task_id(metadata))


def _result_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = result.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _result_text(result: Dict[str, Any]) -> str:
    payload = _result_payload(result)
    for key in ("text", "result", "summary", "message"):
        value = _safe_text(payload.get(key), limit=6000)
        if value:
            return value
    for key in ("summary", "error"):
        value = _safe_text(result.get(key), limit=6000)
        if value:
            return value
    return ""


def _result_files(result: Dict[str, Any]) -> list[dict[str, str]]:
    payload = _result_payload(result)
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raw_files = result.get("files")
    files = normalize_file_rows(raw_files)
    if files:
        return files
    return extract_saved_file_rows(_result_text(result))


def _current_attempt_outcome(result: Dict[str, Any]) -> str:
    payload = _result_payload(result)
    return _safe_text(payload.get("attempt_outcome"), limit=40).lower() or (
        "done" if bool(result.get("ok")) else "blocked"
    )


def _completed_stage_lines(plan: Dict[str, Any]) -> list[str]:
    rows: list[str] = []
    for item in list(plan.get("stages") or []):
        if not isinstance(item, dict):
            continue
        if _safe_text(item.get("status"), limit=40).lower() != "completed":
            continue
        title = _safe_text(item.get("title"), limit=120) or _safe_text(
            item.get("id"), limit=80
        )
        summary = _safe_text(item.get("last_summary"), limit=180) or "已完成"
        rows.append(f"- {title}: {summary}")
    return rows[-4:]


def _diagnostic_summary(
    *,
    result: Dict[str, Any],
    default: str,
) -> str:
    payload = _result_payload(result)
    return (
        _safe_text(payload.get("diagnostic_summary"), limit=1000)
        or _safe_text(result.get("summary"), limit=1000)
        or _safe_text(payload.get("text"), limit=1000)
        or _safe_text(result.get("error"), limit=1000)
        or _safe_text(default, limit=1000)
    )


def _blocking_reason(
    *,
    result: Dict[str, Any],
    stage_title: str,
) -> str:
    payload = _result_payload(result)
    closure_reason = _safe_text(payload.get("closure_reason"), limit=80).lower()
    failure_mode = _safe_text(payload.get("failure_mode"), limit=80).lower()
    diagnostic = _diagnostic_summary(
        result=result,
        default="执行助手在当前阶段未完成收口。",
    )
    if closure_reason in {"max_turn_limit", "tool_budget_guard", "tool_failure_budget"}:
        return f"在“{stage_title or '当前阶段'}”阶段，执行助手在工具调用预算内未完成收口。"
    if closure_reason in {"loop_guard", "semantic_loop_guard"}:
        return f"在“{stage_title or '当前阶段'}”阶段，执行助手出现重复调用趋势，已被循环保护中止。"
    if closure_reason == "timeout":
        return f"在“{stage_title or '当前阶段'}”阶段，执行助手等待过久后超时。"
    if closure_reason == "cancelled":
        return f"在“{stage_title or '当前阶段'}”阶段，这次执行尝试被取消。"
    if failure_mode == "fatal":
        return diagnostic or f"在“{stage_title or '当前阶段'}”阶段，执行助手遇到不可继续的问题。"
    return diagnostic or f"在“{stage_title or '当前阶段'}”阶段，执行助手暂时没有完成当前目标。"


def _closure_cache(metadata: Dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in list(metadata.get("attempt_closures") or [])
        if isinstance(item, dict)
    ][-12:]


def _find_cached_closure(metadata: Dict[str, Any], attempt_task_id: str) -> Dict[str, Any] | None:
    safe_attempt_id = _safe_text(attempt_task_id, limit=80)
    if not safe_attempt_id:
        return None
    for item in reversed(_closure_cache(metadata)):
        if _safe_text(item.get("attempt_task_id"), limit=80) == safe_attempt_id:
            if _safe_text(item.get("kind"), limit=40).lower() == "final":
                return None
            return dict(item)
    return None


def _remember_closure(
    metadata: Dict[str, Any],
    *,
    attempt_task_id: str,
    kind: str,
    text: str = "",
    ui: Dict[str, Any] | None = None,
    files: list[dict[str, str]] | None = None,
) -> Dict[str, Any]:
    updated = dict(metadata or {})
    closures = _closure_cache(updated)
    closures.append(
        {
            "attempt_task_id": _safe_text(attempt_task_id, limit=80),
            "kind": _safe_text(kind, limit=40),
            "text": _safe_text(text, limit=3000),
            "ui": dict(ui or {}),
            "files": [dict(item) for item in list(files or []) if isinstance(item, dict)][
                :8
            ],
            "updated_at": _now_iso(),
        }
    )
    updated["attempt_closures"] = closures[-12:]
    return updated


def _cached_decision(cached: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": _safe_text(cached.get("kind"), limit=40) or "legacy",
        "text": _safe_text(cached.get("text"), limit=3000),
        "ui": dict(cached.get("ui") or {}),
        "files": normalize_file_rows(cached.get("files") or []),
        "auto_repair_allowed": _safe_text(cached.get("kind"), limit=40)
        == "waiting_user",
    }


def _control_intent(text: str) -> str:
    normalized = _safe_text(text, limit=200).lower()
    if normalized in _CONTINUE_CUES:
        return "continue"
    if normalized in _STOP_CUES:
        return "stop"
    return "adjust"


class ManagerClosureService:
    @staticmethod
    def _waiting_ui() -> Dict[str, Any]:
        return {
            "actions": [
                [
                    {"text": "继续执行", "callback_data": "task_continue"},
                    {"text": "停止任务", "callback_data": "task_stop"},
                ]
            ]
        }

    async def _load_session_state(
        self,
        *,
        metadata: Dict[str, Any],
    ) -> tuple[str, str, Any, Dict[str, Any], str, str]:
        task_inbox_id = _task_inbox_id(metadata)
        session_task_id = _session_task_id(metadata) or task_inbox_id
        session_task = await task_inbox.get(task_inbox_id) if task_inbox_id else None
        session_meta = dict((session_task.metadata if session_task else {}) or {})
        task_goal = (
            _safe_text(session_meta.get("task_goal"), limit=6000)
            or _safe_text(metadata.get("task_goal"), limit=6000)
            or _safe_text(session_meta.get("original_user_request"), limit=6000)
            or _safe_text(metadata.get("original_user_request"), limit=6000)
            or _safe_text(getattr(session_task, "goal", ""), limit=6000)
        )
        original_user_request = (
            _safe_text(session_meta.get("original_user_request"), limit=6000)
            or _safe_text(metadata.get("original_user_request"), limit=6000)
            or task_goal
        )
        return (
            task_inbox_id,
            session_task_id,
            session_task,
            session_meta,
            task_goal,
            original_user_request,
        )

    async def _persist_session_metadata(
        self,
        *,
        task_inbox_id: str,
        status: str,
        event: str,
        detail: str,
        metadata: Dict[str, Any],
        extra_fields: Dict[str, Any] | None = None,
    ) -> None:
        if not task_inbox_id:
            return
        fields = {"metadata": metadata}
        fields.update(dict(extra_fields or {}))
        await task_inbox.update_status(
            task_inbox_id,
            status,
            event=event,
            detail=detail[:200],
            **fields,
        )

    async def _set_waiting_user(
        self,
        *,
        user_id: str,
        session_task_id: str,
        task_inbox_id: str,
        task_goal: str,
        original_user_request: str,
        stage_plan: Dict[str, Any],
        stage_id: str,
        stage_title: str,
        attempt_index: int,
        result: Dict[str, Any],
        session_meta: Dict[str, Any],
        attempt_task_id: str,
    ) -> Dict[str, Any]:
        stage_index, stage_total = get_stage_position(stage_plan, stage_id)
        blocking_reason = _blocking_reason(result=result, stage_title=stage_title)
        resume_preview = build_stage_instruction(
            original_request=task_goal,
            plan=stage_plan,
            stage=get_current_stage(stage_plan) or {},
            previous_summary=_safe_text(stage_plan.get("last_stage_summary"), limit=1200),
            previous_output=_safe_text(stage_plan.get("last_stage_output"), limit=2400),
            last_blocking_reason=blocking_reason,
        )
        collected_files = merge_file_rows(
            normalize_file_rows(session_meta.get("collected_files") or []),
            _result_files(result),
        )
        text = format_waiting_user_card(
            session_task_id=session_task_id,
            stage_index=stage_index,
            stage_total=stage_total,
            stage_title=stage_title,
            completed_lines=_completed_stage_lines(stage_plan),
            blocking_reason=blocking_reason,
        )
        merged_metadata = dict(session_meta)
        merged_metadata.update(
            {
                "stage_plan": stage_plan,
                "session_task_id": session_task_id,
                "original_user_request": original_user_request,
                "task_goal": task_goal,
                "last_blocking_reason": blocking_reason,
                "collected_files": collected_files,
                "delivery_state": "pending",
                "last_user_visible_summary": _safe_text(text, limit=2400),
                "resume_window_until": "",
            }
        )
        merged_metadata = _remember_closure(
            merged_metadata,
            attempt_task_id=attempt_task_id,
            kind="waiting_user",
            text=text,
            ui=self._waiting_ui(),
            files=collected_files,
        )
        await self._persist_session_metadata(
            task_inbox_id=task_inbox_id,
            status="waiting_user",
            event="stage_blocked",
            detail=f"stage={stage_index}/{max(1, stage_total)}:{stage_title or stage_id}",
            metadata=merged_metadata,
        )
        if user_id:
            await heartbeat_store.update_session_active_task(
                user_id,
                session_task_id=session_task_id,
                task_inbox_id=task_inbox_id,
                status="waiting_user",
                goal=task_goal,
                stage_index=stage_index,
                stage_total=stage_total,
                stage_id=stage_id,
                stage_title=stage_title,
                attempt_index=max(1, int(attempt_index or 0)),
                result_summary=_diagnostic_summary(
                    result=result,
                    default=blocking_reason,
                ),
                delivery_state="pending",
                last_user_visible_summary=_safe_text(text, limit=2400),
                resume_window_until="",
                needs_confirmation=True,
                confirmation_deadline="",
                last_blocking_reason=blocking_reason,
                resume_instruction_preview=resume_preview,
                adjustments_count=count_adjustments(stage_plan),
            )
            await heartbeat_store.release_lock(user_id)
            await heartbeat_store.append_session_event(
                user_id,
                f"stage_blocked:{session_task_id or task_inbox_id}:{stage_id or 'stage'}",
            )
        return {
            "kind": "waiting_user",
            "text": text,
            "ui": self._waiting_ui(),
            "files": collected_files,
            "auto_repair_allowed": True,
        }

    async def resolve_attempt(
        self,
        *,
        task: TaskEnvelope,
        result: Dict[str, Any],
        platform: str,
        chat_id: str,
    ) -> Dict[str, Any]:
        metadata = dict(task.metadata or {})
        if not _is_staged_session(metadata):
            return {"kind": "legacy", "auto_repair_allowed": True}

        (
            task_inbox_id,
            session_task_id,
            session_task,
            session_meta,
            task_goal,
            original_user_request,
        ) = await self._load_session_state(metadata=metadata)
        if session_task is None or not task_inbox_id:
            return {"kind": "legacy", "auto_repair_allowed": True}

        cached = _find_cached_closure(session_meta, task.task_id)
        if cached is not None:
            return _cached_decision(cached)

        user_id = (
            _safe_text(metadata.get("user_id"), limit=80)
            or _safe_text(getattr(session_task, "user_id", ""), limit=80)
        )
        stage_plan = normalize_stage_plan(
            session_meta.get("stage_plan")
            if isinstance(session_meta.get("stage_plan"), dict)
            else metadata.get("stage_plan")
            if isinstance(metadata.get("stage_plan"), dict)
            else None,
            original_request=task_goal or task.instruction,
        )
        stage_id = (
            _safe_text(metadata.get("stage_id"), limit=80)
            or _safe_text(stage_plan.get("current_stage_id"), limit=80)
        )
        current_stage = get_current_stage(stage_plan) or {}
        if not stage_id:
            stage_id = _safe_text(current_stage.get("id"), limit=80)
        stage_title = _safe_text(
            metadata.get("stage_title") or current_stage.get("title"),
            limit=200,
        )
        attempt_index = max(1, int(metadata.get("attempt_index") or 1))
        stage_plan = merge_collected_files(stage_plan, files=_result_files(result))

        if bool(result.get("ok")) and _current_attempt_outcome(result) == "done":
            summary = _diagnostic_summary(
                result=result,
                default="当前阶段已完成。",
            )
            output = _safe_text(_result_text(result) or summary, limit=4000)
            stage_plan = mark_stage_completed(
                stage_plan,
                stage_id=stage_id,
                summary=summary,
                output=output,
            )
            next_stage = get_current_stage(stage_plan)
            if (
                next_stage is not None
                and _safe_text(next_stage.get("id"), limit=80) == stage_id
                and _safe_text(next_stage.get("status"), limit=40).lower()
                in {"completed", "done"}
            ):
                next_stage = None
            merged_files = merge_file_rows(
                normalize_file_rows(stage_plan.get("collected_files") or []),
                _result_files(result),
            )
            merged_metadata = dict(session_meta)
            merged_metadata.update(
                {
                    "stage_plan": stage_plan,
                    "session_task_id": session_task_id,
                    "task_goal": task_goal,
                    "original_user_request": original_user_request,
                    "last_blocking_reason": "",
                    "collected_files": merged_files,
                    "delivery_state": "pending",
                    "last_user_visible_summary": _safe_text(output or summary, limit=2400),
                }
            )
            if next_stage is None:
                final_result = dict(result)
                final_payload = _result_payload(final_result)
                final_payload["files"] = merged_files
                # Final staged output is already the user-facing deliverable.
                final_payload["delivery_mode"] = "full_text"
                final_payload["user_facing_output"] = True
                final_result["payload"] = final_payload
                merged_metadata["resume_window_until"] = _future_iso(15 * 60)
                await self._persist_session_metadata(
                    task_inbox_id=task_inbox_id,
                    status="completed",
                    event="session_completed",
                    detail=summary,
                    metadata=merged_metadata,
                    extra_fields={
                        "result": final_result,
                        "final_output": output,
                        "output": final_payload,
                    },
                )
                if user_id:
                    await heartbeat_store.update_session_active_task(
                        user_id,
                        status="completed",
                        needs_confirmation=False,
                        confirmation_deadline="",
                        result_summary=summary,
                        delivery_state="pending",
                        last_user_visible_summary=_safe_text(output or summary, limit=2400),
                        resume_window_until=_future_iso(15 * 60),
                        clear_active=True,
                        last_blocking_reason="",
                        resume_instruction_preview="",
                        adjustments_count=count_adjustments(stage_plan),
                    )
                    await heartbeat_store.append_session_event(
                        user_id,
                        f"session_completed:{session_task_id or task_inbox_id}",
                    )
                return {
                    "kind": "final",
                    "result": final_result,
                    "auto_repair_allowed": False,
                }

            next_stage_id = _safe_text(next_stage.get("id"), limit=80)
            next_stage_title = _safe_text(next_stage.get("title"), limit=200)
            stage_index, stage_total = get_stage_position(stage_plan, next_stage_id)
            await self._persist_session_metadata(
                task_inbox_id=task_inbox_id,
                status="running",
                event="stage_completed",
                detail=f"next={stage_index}/{max(1, stage_total)}:{next_stage_title or next_stage_id}",
                metadata=merged_metadata,
            )
            dispatch_metadata = dict(merged_metadata)
            dispatch_metadata.update(
                {
                    "user_id": user_id,
                    "platform": platform,
                    "chat_id": chat_id,
                    "task_inbox_id": task_inbox_id,
                    "session_task_id": session_task_id,
                    "last_blocking_reason": "",
                }
            )
            dispatch_result = await manager_dispatch_service.dispatch_worker(
                instruction=task_goal or task.instruction,
                worker_id=_safe_text(
                    getattr(session_task, "assigned_worker_id", "") or task.worker_id,
                    limit=80,
                ),
                source="worker_stage_continue",
                metadata=dispatch_metadata,
            )
            if not bool(dispatch_result.get("ok")):
                fallback_result = {
                    "ok": False,
                    "summary": _safe_text(
                        dispatch_result.get("summary")
                        or dispatch_result.get("message")
                        or "Manager 在推进下一阶段时未能成功派发新的执行尝试。",
                        limit=1000,
                    ),
                    "error": _safe_text(
                        dispatch_result.get("error")
                        or dispatch_result.get("message")
                        or "dispatch failed",
                        limit=1000,
                    ),
                    "payload": {
                        "text": _safe_text(
                            dispatch_result.get("message")
                            or dispatch_result.get("summary")
                            or "Manager 在推进下一阶段时未能成功派发新的执行尝试。",
                            limit=1000,
                        ),
                        "attempt_outcome": "blocked",
                        "closure_reason": "dispatch_failed",
                        "failure_mode": "recoverable",
                        "diagnostic_summary": _safe_text(
                            dispatch_result.get("message")
                            or dispatch_result.get("summary")
                            or "Manager 在推进下一阶段时未能成功派发新的执行尝试。",
                            limit=1000,
                        ),
                    },
                }
                return await self._set_waiting_user(
                    user_id=user_id,
                    session_task_id=session_task_id,
                    task_inbox_id=task_inbox_id,
                    task_goal=task_goal,
                    original_user_request=original_user_request,
                    stage_plan=stage_plan,
                    stage_id=next_stage_id,
                    stage_title=next_stage_title,
                    attempt_index=max(1, stage_index),
                    result=fallback_result,
                    session_meta=merged_metadata,
                    attempt_task_id=task.task_id,
                )
            try:
                dispatched_worker_id = _safe_text(dispatch_result.get("worker_id"), limit=80)
                if dispatched_worker_id:
                    await task_inbox.assign_worker(
                        task_inbox_id,
                        worker_id=dispatched_worker_id,
                        reason=_safe_text(
                            dispatch_result.get("selection_reason"), limit=120
                        ),
                        manager_id="core-manager",
                    )
            except Exception:
                logger.debug("Failed to refresh assigned worker after next stage dispatch", exc_info=True)

            text = format_stage_continue_card(
                session_task_id=session_task_id,
                stage_index=stage_index,
                stage_total=stage_total,
                stage_title=next_stage_title,
            )
            latest_task = await task_inbox.get(task_inbox_id)
            latest_metadata = dict((latest_task.metadata if latest_task else {}) or {})
            latest_metadata["delivery_state"] = "pending"
            latest_metadata["last_user_visible_summary"] = _safe_text(text, limit=2400)
            latest_metadata = _remember_closure(
                latest_metadata,
                attempt_task_id=task.task_id,
                kind="next_stage",
                text=text,
            )
            await self._persist_session_metadata(
                task_inbox_id=task_inbox_id,
                status="running",
                event="stage_advanced",
                detail=f"next={stage_index}/{max(1, stage_total)}:{next_stage_title or next_stage_id}",
                metadata=latest_metadata,
            )
            if user_id:
                await heartbeat_store.append_session_event(
                    user_id,
                    f"stage_completed:{session_task_id or task_inbox_id}:{stage_id}",
                )
            return {
                "kind": "next_stage",
                "text": text,
                "ui": {},
                "files": [],
                "auto_repair_allowed": False,
            }

        stage_plan = mark_stage_blocked(
            stage_plan,
            stage_id=stage_id,
            summary=_diagnostic_summary(
                result=result,
                default="当前阶段未完成。",
            ),
            error=_safe_text(result.get("error") or _result_text(result), limit=1000),
        )
        return await self._set_waiting_user(
            user_id=user_id,
            session_task_id=session_task_id,
            task_inbox_id=task_inbox_id,
            task_goal=task_goal,
            original_user_request=original_user_request,
            stage_plan=stage_plan,
            stage_id=stage_id,
            stage_title=stage_title,
            attempt_index=attempt_index,
            result=result,
            session_meta=session_meta,
            attempt_task_id=task.task_id,
        )

    async def resume_waiting_task(
        self,
        *,
        user_id: str,
        user_message: str,
        source: str = "text",
    ) -> Dict[str, Any]:
        safe_user_id = _safe_text(user_id, limit=80)
        active_task = await heartbeat_store.get_session_active_task(safe_user_id)
        if not active_task or _safe_text(active_task.get("status"), limit=40) != "waiting_user":
            return {
                "ok": False,
                "message": "当前没有等待继续的任务。",
            }

        task_inbox_id = _safe_text(
            active_task.get("task_inbox_id") or active_task.get("session_task_id"),
            limit=80,
        )
        session_task = await task_inbox.get(task_inbox_id) if task_inbox_id else None
        if session_task is None:
            return {
                "ok": False,
                "message": "找不到对应的任务上下文，无法继续执行。",
            }

        session_meta = dict(session_task.metadata or {})
        task_goal = (
            _safe_text(session_meta.get("task_goal"), limit=6000)
            or _safe_text(session_meta.get("original_user_request"), limit=6000)
            or _safe_text(session_task.goal, limit=6000)
        )
        original_user_request = (
            _safe_text(session_meta.get("original_user_request"), limit=6000)
            or task_goal
        )
        stage_plan = normalize_stage_plan(
            session_meta.get("stage_plan")
            if isinstance(session_meta.get("stage_plan"), dict)
            else None,
            original_request=task_goal,
        )
        intent = _control_intent(user_message)
        if intent == "stop":
            return {
                "ok": False,
                "message": "该接口不处理 stop，请由上层走停止逻辑。",
            }
        if intent == "adjust":
            stage_plan = add_adjustment(
                stage_plan,
                message=user_message,
                source=source,
            )

        stage = get_current_stage(stage_plan) or {}
        stage_id = _safe_text(stage.get("id"), limit=80)
        stage_title = _safe_text(stage.get("title"), limit=200)
        delivery_target = await heartbeat_store.get_delivery_target(safe_user_id)
        dispatch_metadata = dict(session_meta)
        dispatch_metadata.update(
            {
                "user_id": safe_user_id,
                "platform": _safe_text(delivery_target.get("platform"), limit=64),
                "chat_id": _safe_text(delivery_target.get("chat_id"), limit=128),
                "task_inbox_id": task_inbox_id,
                "session_task_id": _safe_text(
                    active_task.get("session_task_id") or task_inbox_id,
                    limit=80,
                ),
                "original_user_request": original_user_request,
                "task_goal": task_goal,
                "stage_plan": stage_plan,
                "last_blocking_reason": _safe_text(
                    active_task.get("last_blocking_reason"), limit=1200
                ),
            }
        )
        dispatch_result = await manager_dispatch_service.dispatch_worker(
            instruction=task_goal or session_task.goal,
            worker_id=_safe_text(
                getattr(session_task, "assigned_worker_id", ""),
                limit=80,
            ),
            source="waiting_user_resume",
            metadata=dispatch_metadata,
        )
        if not bool(dispatch_result.get("ok")):
            return {
                "ok": False,
                "message": _safe_text(
                    dispatch_result.get("message")
                    or dispatch_result.get("summary")
                    or "重新派发任务失败，请稍后重试。",
                    limit=500,
                ),
            }

        try:
            dispatched_worker_id = _safe_text(dispatch_result.get("worker_id"), limit=80)
            if dispatched_worker_id:
                await task_inbox.assign_worker(
                    task_inbox_id,
                    worker_id=dispatched_worker_id,
                    reason=_safe_text(dispatch_result.get("selection_reason"), limit=120),
                    manager_id="core-manager",
                )
        except Exception:
            logger.debug("Failed to sync assigned worker on resume", exc_info=True)

        await heartbeat_store.append_session_event(
            safe_user_id,
            (
                f"user_adjust_resume:{task_inbox_id}:{stage_id}"
                if intent == "adjust"
                else f"user_continue_resume:{task_inbox_id}:{stage_id}"
            ),
        )
        stage_index = max(0, int(dispatch_result.get("payload", {}).get("stage_index") or 0))
        stage_total = max(0, int(dispatch_result.get("payload", {}).get("stage_total") or 0))
        stage_hint = (
            f"阶段 {stage_index}/{max(1, stage_total)}"
            if stage_index > 0 and stage_total > 0
            else (stage_title or "当前阶段")
        )
        if intent == "adjust":
            message = f"✅ 已记录你的补充说明，正在继续推进 {stage_hint}。"
        else:
            message = f"✅ 已恢复执行，正在继续推进 {stage_hint}。"
        return {
            "ok": True,
            "message": message,
            "task_id": _safe_text(
                dispatch_result.get("session_task_id")
                or dispatch_result.get("payload", {}).get("task_id"),
                limit=80,
            ),
        }


manager_closure_service = ManagerClosureService()
