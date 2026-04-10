from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from core.file_artifacts import extract_saved_file_rows, merge_file_rows, normalize_file_rows
from core.heartbeat_store import heartbeat_store
from core.subagent_supervisor import subagent_supervisor
from core.task_cards import format_stage_continue_card, format_waiting_user_card
from core.task_inbox import task_inbox
from core.tool_registry import tool_registry
from ikaros.planning.stage_planner import (
    add_adjustment,
    build_stage_instruction,
    count_adjustments,
    get_current_stage,
    get_stage_position,
    mark_stage_blocked,
    mark_stage_completed,
    mark_stage_running,
    merge_collected_files,
    normalize_stage_plan,
)
from shared.contracts.dispatch import TaskEnvelope

logger = logging.getLogger(__name__)

_FINAL_DELIVERY_BLOCK_MARKERS = (
    "不具备直接向用户交付",
    "不能直接交付给最终用户",
    "尚未达到可直接交付",
    "尚未形成用户可读交付",
    "可交付：当前验证结论",
    "不可交付：正式版",
    "如果需要继续",
    "需重新检索",
    "回到检索阶段",
)
_DEFAULT_SUBAGENT_TOOL_EXCLUDES = {
    "await_subagents",
    "spawn_subagent",
    "task_tracker",
}


def _safe_text(value: Any, *, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _normalize_tokens(values: Any) -> list[str]:
    rows: list[str] = []
    for item in list(values or []):
        token = str(item or "").strip()
        if token and token not in rows:
            rows.append(token)
    return rows


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


def _final_stage_contradicts_completion(result: Dict[str, Any]) -> bool:
    text = "\n".join(
        [
            _safe_text(_result_text(result), limit=6000),
            _safe_text(result.get("summary"), limit=2000),
        ]
    ).strip()
    if not text:
        return False
    return any(marker in text for marker in _FINAL_DELIVERY_BLOCK_MARKERS)


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
def _resolve_tool_scope(*metadata_sources: Dict[str, Any]) -> tuple[list[str], list[str]]:
    allowed_tools: list[str] = []
    allowed_skills: list[str] = []
    for source in metadata_sources:
        if not isinstance(source, dict):
            continue
        scope = source.get("tool_scope")
        if isinstance(scope, dict):
            if not allowed_tools:
                allowed_tools = _normalize_tokens(scope.get("allowed_tools"))
            if not allowed_skills:
                allowed_skills = _normalize_tokens(scope.get("allowed_skills"))
        if not allowed_tools:
            allowed_tools = _normalize_tokens(source.get("allowed_tool_names"))
        if not allowed_skills:
            allowed_skills = _normalize_tokens(source.get("allowed_skill_names"))
        if allowed_tools:
            break

    if not allowed_tools:
        allowed_tools = [
            name
            for name in tool_registry.get_ikaros_tool_names()
            if name and name not in _DEFAULT_SUBAGENT_TOOL_EXCLUDES
        ]
    return allowed_tools, allowed_skills


def _skill_tool_name(skill_name: str) -> str:
    safe_name = _safe_text(skill_name, limit=120)
    if not safe_name:
        return ""
    return f"ext_{safe_name.replace('-', '_')}"


def _finalize_tool_scope(
    allowed_tools: Any,
    allowed_skills: Any,
) -> tuple[list[str], list[str]]:
    normalized_tools = _normalize_tokens(allowed_tools)
    normalized_skills = _normalize_tokens(allowed_skills)
    if normalized_skills:
        if "load_skill" not in normalized_tools:
            normalized_tools.append("load_skill")
        for skill_name in normalized_skills:
            tool_name = _skill_tool_name(skill_name)
            if tool_name and tool_name not in normalized_tools:
                normalized_tools.append(tool_name)
    return normalized_tools, normalized_skills


async def _augment_resume_tool_scope(
    *,
    session_meta: Dict[str, Any],
    task_goal: str,
    stage_plan: Dict[str, Any],
    user_message: str,
    last_blocking_reason: str = "",
) -> tuple[list[str], list[str]]:
    allowed_tools, allowed_skills = _resolve_tool_scope(session_meta)
    allowed_tools, allowed_skills = _finalize_tool_scope(
        allowed_tools,
        allowed_skills,
    )

    current_stage = get_current_stage(stage_plan) or {}
    routing_text = "\n".join(
        [
            item
            for item in (
                _safe_text(task_goal, limit=4000),
                _safe_text(current_stage.get("goal"), limit=4000),
                _safe_text(last_blocking_reason, limit=1200),
                _safe_text(user_message, limit=1200),
            )
            if str(item).strip()
        ]
    ).strip()
    if not routing_text:
        return allowed_tools, allowed_skills

    try:
        from core.extension_router import ExtensionRouter
        from services.intent_router import intent_router

        candidates = ExtensionRouter().route(routing_text, max_candidates=24)
        if not candidates:
            return allowed_tools, allowed_skills
        decision = await intent_router.route(
            dialog_messages=[{"role": "user", "content": routing_text}],
            candidates=candidates,
            max_candidates=8,
        )
        selected_names = {
            str(item or "").strip()
            for item in list(getattr(decision, "candidate_skills", []) or [])
            if str(item or "").strip()
        }
        if selected_names:
            selected_skills = [
                str(getattr(candidate, "name", "") or "").strip()
                for candidate in candidates
                if str(getattr(candidate, "name", "") or "").strip() in selected_names
            ]
            selected_tools = [
                str(getattr(candidate, "tool_name", "") or "").strip()
                for candidate in candidates
                if str(getattr(candidate, "name", "") or "").strip() in selected_names
            ]
            allowed_skills = _normalize_tokens([*allowed_skills, *selected_skills])
            allowed_tools = _normalize_tokens([*allowed_tools, *selected_tools])
            logger.info(
                "resume_waiting_task supplemented skill scope: selected=%s merged=%s",
                selected_skills or "none",
                allowed_skills or "none",
            )
    except Exception:
        logger.debug("resume skill scope supplementation failed", exc_info=True)

    return _finalize_tool_scope(allowed_tools, allowed_skills)


def _subagent_timeout_sec(*metadata_sources: Dict[str, Any]) -> int:
    for source in metadata_sources:
        if not isinstance(source, dict):
            continue
        try:
            timeout = int(source.get("subagent_timeout_sec") or 0)
        except Exception:
            timeout = 0
        if timeout > 0:
            return max(30, timeout)
    return 900


class IkarosClosureService:
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

    async def _start_stage_attempt(
        self,
        *,
        user_id: str,
        platform: str,
        chat_id: str,
        task_inbox_id: str,
        session_task_id: str,
        task_goal: str,
        original_user_request: str,
        stage_plan: Dict[str, Any],
        session_meta: Dict[str, Any],
        last_blocking_reason: str = "",
        source: str,
    ) -> Dict[str, Any]:
        current_stage = get_current_stage(stage_plan) or {}
        stage_id = _safe_text(current_stage.get("id"), limit=80)
        stage_title = _safe_text(current_stage.get("title"), limit=200)
        if not stage_id:
            return {
                "ok": False,
                "message": "当前阶段信息缺失，无法继续执行。",
            }

        prepared_plan = mark_stage_running(stage_plan, stage_id=stage_id)
        prepared_stage = get_current_stage(prepared_plan) or current_stage
        stage_index, stage_total = get_stage_position(prepared_plan, stage_id)
        attempt_index = max(1, int(prepared_stage.get("attempt_count") or 1))
        prepared_goal = build_stage_instruction(
            original_request=task_goal,
            plan=prepared_plan,
            stage=prepared_stage,
            resolved_task_goal=task_goal,
            previous_summary=_safe_text(prepared_plan.get("last_stage_summary"), limit=1200),
            previous_output=_safe_text(prepared_plan.get("last_stage_output"), limit=2400),
            last_blocking_reason=last_blocking_reason,
        )
        allowed_tools, allowed_skills = _resolve_tool_scope(session_meta)
        allowed_tools, allowed_skills = _finalize_tool_scope(
            allowed_tools,
            allowed_skills,
        )
        timeout_sec = _subagent_timeout_sec(session_meta)
        tool_scope = {
            "allowed_tools": list(allowed_tools),
            "allowed_skills": list(allowed_skills),
        }
        session_metadata = dict(session_meta)
        session_metadata.update(
            {
                "staged_session": True,
                "session_task_id": session_task_id,
                "task_inbox_id": task_inbox_id,
                "task_goal": task_goal,
                "original_user_request": original_user_request,
                "stage_plan": prepared_plan,
                "stage_id": stage_id,
                "stage_title": stage_title,
                "stage_index": stage_index,
                "stage_total": stage_total,
                "attempt_index": attempt_index,
                "resume_instruction_preview": _safe_text(prepared_goal, limit=1200),
                "adjustments_count": count_adjustments(prepared_plan),
                "tool_scope": tool_scope,
                "executor_type": "subagent",
                "last_blocking_reason": "",
                "delivery_state": "pending",
            }
        )
        spawn_result = await subagent_supervisor.spawn(
            ctx=None,
            goal=prepared_goal,
            allowed_tools=allowed_tools,
            allowed_skills=allowed_skills,
            mode="detached",
            timeout_sec=timeout_sec,
            parent_task_id=session_task_id,
            parent_task_inbox_id=task_inbox_id,
            user_id_override=user_id,
            platform_override=platform,
            chat_id_override=chat_id,
            task_metadata={
                "staged_session": True,
                "task_inbox_id": task_inbox_id,
                "session_task_id": session_task_id,
                "task_goal": task_goal,
                "original_user_request": original_user_request,
                "stage_plan": prepared_plan,
                "stage_id": stage_id,
                "stage_title": stage_title,
                "stage_index": stage_index,
                "stage_total": stage_total,
                "attempt_index": attempt_index,
                "last_blocking_reason": last_blocking_reason,
                "tool_scope": tool_scope,
                "source": source,
                "user_id": user_id,
                "platform": platform,
                "chat_id": chat_id,
            },
        )
        if not bool(spawn_result.get("ok")):
            return {
                "ok": False,
                "message": _safe_text(
                    spawn_result.get("message")
                    or spawn_result.get("summary")
                    or "无法启动阶段子任务。",
                    limit=1000,
                ),
                "stage_plan": prepared_plan,
                "stage_id": stage_id,
                "stage_title": stage_title,
            }

        subagent_id = _safe_text(spawn_result.get("subagent_id"), limit=80)
        detached_task_id = _safe_text(spawn_result.get("task_id"), limit=80)
        session_metadata["subagent_ids"] = [subagent_id] if subagent_id else []
        session_metadata["active_subagent_task_id"] = detached_task_id
        await self._persist_session_metadata(
            task_inbox_id=task_inbox_id,
            status="running",
            event="stage_attempt_started",
            detail=f"subagent={subagent_id}; stage={stage_index}/{max(1, stage_total)}:{stage_title or stage_id}",
            metadata=session_metadata,
        )
        if user_id:
            await heartbeat_store.update_session_active_task(
                user_id,
                session_task_id=session_task_id,
                task_inbox_id=task_inbox_id,
                status="running",
                goal=task_goal,
                stage_index=stage_index,
                stage_total=stage_total,
                stage_id=stage_id,
                stage_title=stage_title,
                attempt_index=attempt_index,
                result_summary=(
                    f"正在推进阶段 {stage_index}/{max(1, stage_total)}："
                    f"{stage_title or stage_id or '执行任务'}"
                )[:500],
                needs_confirmation=False,
                confirmation_deadline="",
                last_blocking_reason="",
                resume_instruction_preview=_safe_text(prepared_goal, limit=2000),
                adjustments_count=count_adjustments(prepared_plan),
            )
        return {
            "ok": True,
            "subagent_id": subagent_id,
            "task_id": detached_task_id,
            "stage_plan": prepared_plan,
            "stage_id": stage_id,
            "stage_title": stage_title,
            "stage_index": stage_index,
            "stage_total": stage_total,
        }

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
        stage_index, stage_total = get_stage_position(stage_plan, stage_id)
        stage_plan = merge_collected_files(stage_plan, files=_result_files(result))

        if bool(result.get("ok")) and _current_attempt_outcome(result) == "done":
            if (
                stage_total > 0
                and stage_index == stage_total
                and _final_stage_contradicts_completion(result)
            ):
                stage_plan = mark_stage_blocked(
                    stage_plan,
                    stage_id=stage_id,
                    summary=_diagnostic_summary(
                        result=result,
                        default="最后阶段未产出可直接交付的结果。",
                    ),
                    error=_safe_text(_result_text(result), limit=1000),
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
            start_result = await self._start_stage_attempt(
                user_id=user_id,
                platform=platform,
                chat_id=chat_id,
                task_inbox_id=task_inbox_id,
                session_task_id=session_task_id,
                task_goal=task_goal or task.instruction,
                original_user_request=original_user_request,
                stage_plan=stage_plan,
                session_meta=merged_metadata,
                last_blocking_reason="",
                source="stage_continue",
            )
            if not bool(start_result.get("ok")):
                blocked_plan = mark_stage_blocked(
                    stage_plan,
                    stage_id=next_stage_id,
                    summary=_safe_text(
                        start_result.get("message") or "Ikaros 未能启动下一阶段。",
                        limit=1000,
                    ),
                    error=_safe_text(
                        start_result.get("message") or "subagent spawn failed",
                        limit=1000,
                    ),
                )
                fallback_result = {
                    "ok": False,
                    "summary": _safe_text(
                        start_result.get("message")
                        or "Ikaros 在推进下一阶段时未能启动新的子任务。",
                        limit=1000,
                    ),
                    "error": _safe_text(
                        start_result.get("message") or "subagent spawn failed",
                        limit=1000,
                    ),
                    "payload": {
                        "text": _safe_text(
                            start_result.get("message")
                            or "Ikaros 在推进下一阶段时未能启动新的子任务。",
                            limit=1000,
                        ),
                        "attempt_outcome": "blocked",
                        "closure_reason": "spawn_failed",
                        "failure_mode": "recoverable",
                        "diagnostic_summary": _safe_text(
                            start_result.get("message")
                            or "Ikaros 在推进下一阶段时未能启动新的子任务。",
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
                    stage_plan=blocked_plan,
                    stage_id=next_stage_id,
                    stage_title=next_stage_title,
                    attempt_index=max(1, stage_index),
                    result=fallback_result,
                    session_meta=merged_metadata,
                    attempt_task_id=task.task_id,
                )

            text = format_stage_continue_card(
                session_task_id=session_task_id,
                stage_index=max(0, int(start_result.get("stage_index") or stage_index)),
                stage_total=max(0, int(start_result.get("stage_total") or stage_total)),
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
        safe_user_message = _safe_text(user_message, limit=2000)
        if safe_user_message:
            stage_plan = add_adjustment(
                stage_plan,
                message=safe_user_message,
                source=source,
            )

        stage = get_current_stage(stage_plan) or {}
        stage_id = _safe_text(stage.get("id"), limit=80)
        stage_title = _safe_text(stage.get("title"), limit=200)
        delivery_target = await heartbeat_store.get_delivery_target(safe_user_id)
        allowed_tools, allowed_skills = await _augment_resume_tool_scope(
            session_meta=session_meta,
            task_goal=task_goal or session_task.goal,
            stage_plan=stage_plan,
            user_message=safe_user_message,
            last_blocking_reason=_safe_text(
                active_task.get("last_blocking_reason"), limit=1200
            ),
        )
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
                "tool_scope": {
                    "allowed_tools": list(allowed_tools),
                    "allowed_skills": list(allowed_skills),
                },
                "allowed_tool_names": list(allowed_tools),
                "allowed_skill_names": list(allowed_skills),
            }
        )
        start_result = await self._start_stage_attempt(
            user_id=safe_user_id,
            platform=_safe_text(delivery_target.get("platform"), limit=64),
            chat_id=_safe_text(delivery_target.get("chat_id"), limit=128),
            task_inbox_id=task_inbox_id,
            session_task_id=_safe_text(
                active_task.get("session_task_id") or task_inbox_id,
                limit=80,
            ),
            task_goal=task_goal or session_task.goal,
            original_user_request=original_user_request,
            stage_plan=stage_plan,
            session_meta=dispatch_metadata,
            last_blocking_reason=_safe_text(
                active_task.get("last_blocking_reason"), limit=1200
            ),
            source="waiting_user_resume",
        )
        if not bool(start_result.get("ok")):
            return {
                "ok": False,
                "message": _safe_text(
                    start_result.get("message")
                    or "重新派发任务失败，请稍后重试。",
                    limit=500,
                ),
            }

        await heartbeat_store.append_session_event(
            safe_user_id,
            f"user_resume:{task_inbox_id}:{stage_id}",
        )
        stage_index = max(0, int(start_result.get("stage_index") or 0))
        stage_total = max(0, int(start_result.get("stage_total") or 0))
        stage_hint = (
            f"阶段 {stage_index}/{max(1, stage_total)}"
            if stage_index > 0 and stage_total > 0
            else (stage_title or "当前阶段")
        )
        if safe_user_message:
            message = f"✅ 已收到你的回复，正在继续推进 {stage_hint}。"
        else:
            message = f"✅ 已恢复执行，正在继续推进 {stage_hint}。"
        return {
            "ok": True,
            "message": message,
            "task_id": _safe_text(start_result.get("task_id"), limit=80),
        }


ikaros_closure_service = IkarosClosureService()
