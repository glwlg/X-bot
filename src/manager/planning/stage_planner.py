from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


StagePlan = Dict[str, Any]


def _safe_text(value: Any, *, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _is_complex_instruction(text: str) -> bool:
    raw = _safe_text(text, limit=6000)
    if not raw:
        return False

    score = 0
    lowered = raw.lower()
    if len(raw) >= 120:
        score += 1
    if "\n" in raw:
        score += 1
    if raw.count("，") >= 3 or raw.count("。") >= 2:
        score += 1
    if any(token in lowered for token in ("1.", "2.", "- ", "* ", "• ")):
        score += 1
    if any(
        token in raw
        for token in (
            "然后",
            "接着",
            "再",
            "最后",
            "同时",
            "并且",
            "阶段",
            "步骤",
            "调研",
            "分析",
            "实现",
            "修复",
            "部署",
            "验证",
            "报告",
        )
    ):
        score += 1
    return score >= 2


def _default_stage(
    *,
    stage_id: str,
    title: str,
    goal: str,
    success_signal: str,
) -> Dict[str, Any]:
    return {
        "id": stage_id,
        "title": title,
        "goal": goal,
        "success_signal": success_signal,
        "executor": "subagent",
        "status": "pending",
        "attempt_count": 0,
        "last_summary": "",
        "last_output": "",
        "last_error": "",
    }


def _fresh_stage_plan(original_request: str) -> StagePlan:
    request = _safe_text(original_request, limit=6000)
    if _is_complex_instruction(request):
        stages = [
            _default_stage(
                stage_id="stage-1",
                title="收集信息与确认约束",
                goal=f"先梳理任务输入、上下文、依赖和约束，仅完成进入执行前必须的确认。原始任务：{request}",
                success_signal="已明确后续执行所需材料、约束与关键依赖。",
            ),
            _default_stage(
                stage_id="stage-2",
                title="执行主要任务",
                goal=f"基于前一阶段掌握的信息，完成该任务的主要执行部分。原始任务：{request}",
                success_signal="主要工作已经完成，具备进入验证和整理阶段的条件。",
            ),
            _default_stage(
                stage_id="stage-3",
                title="验证结果并整理交付",
                goal=f"验证前面阶段的结果，整理为可直接交付给用户的结论、产物或说明。原始任务：{request}",
                success_signal="结果已验证，并具备最终交付所需的摘要或附件。",
            ),
        ]
        complexity = "complex"
    else:
        stages = [
            _default_stage(
                stage_id="stage-1",
                title="执行任务",
                goal=request,
                success_signal="给出可直接交付的结果。",
            )
        ]
        complexity = "simple"

    return {
        "mode": "staged",
        "complexity": complexity,
        "original_request": request,
        "stages": stages,
        "current_stage_id": stages[0]["id"],
        "completed_stage_ids": [],
        "blocked_stage_id": "",
        "attempt_count": 0,
        "adjustments": [],
        "collected_files": [],
        "last_stage_summary": "",
        "last_stage_output": "",
    }


def normalize_stage_plan(
    raw_plan: Dict[str, Any] | None,
    *,
    original_request: str,
) -> StagePlan:
    request = _safe_text(original_request, limit=6000)
    if not isinstance(raw_plan, dict):
        return _fresh_stage_plan(request)

    plan = _fresh_stage_plan(request)
    plan["mode"] = _safe_text(raw_plan.get("mode") or "staged", limit=32) or "staged"
    plan["complexity"] = (
        _safe_text(raw_plan.get("complexity") or plan["complexity"], limit=32)
        or plan["complexity"]
    )
    plan["attempt_count"] = max(0, int(raw_plan.get("attempt_count") or 0))
    plan["completed_stage_ids"] = [
        _safe_text(item, limit=80)
        for item in list(raw_plan.get("completed_stage_ids") or [])
        if _safe_text(item, limit=80)
    ]
    plan["blocked_stage_id"] = _safe_text(raw_plan.get("blocked_stage_id"), limit=80)
    plan["last_stage_summary"] = _safe_text(raw_plan.get("last_stage_summary"), limit=1000)
    plan["last_stage_output"] = _safe_text(raw_plan.get("last_stage_output"), limit=4000)
    plan["adjustments"] = [
        dict(item)
        for item in list(raw_plan.get("adjustments") or [])
        if isinstance(item, dict)
    ][-20:]
    plan["collected_files"] = [
        dict(item)
        for item in list(raw_plan.get("collected_files") or [])
        if isinstance(item, dict)
    ][-40:]

    raw_stages = list(raw_plan.get("stages") or [])
    normalized_stages: List[Dict[str, Any]] = []
    for index, row in enumerate(raw_stages, start=1):
        if not isinstance(row, dict):
            continue
        fallback = plan["stages"][min(index - 1, len(plan["stages"]) - 1)]
        stage = _default_stage(
            stage_id=_safe_text(row.get("id") or fallback["id"], limit=80)
            or f"stage-{index}",
            title=_safe_text(row.get("title") or fallback["title"], limit=200)
            or fallback["title"],
            goal=_safe_text(row.get("goal") or fallback["goal"], limit=4000)
            or fallback["goal"],
            success_signal=_safe_text(
                row.get("success_signal") or fallback["success_signal"], limit=400
            )
            or fallback["success_signal"],
        )
        executor = _safe_text(row.get("executor") or "subagent", limit=40).lower()
        stage["executor"] = executor or "subagent"
        stage["status"] = _safe_text(row.get("status") or "pending", limit=40).lower() or "pending"
        stage["attempt_count"] = max(0, int(row.get("attempt_count") or 0))
        stage["last_summary"] = _safe_text(row.get("last_summary"), limit=1000)
        stage["last_output"] = _safe_text(row.get("last_output"), limit=4000)
        stage["last_error"] = _safe_text(row.get("last_error"), limit=1000)
        normalized_stages.append(stage)

    if normalized_stages:
        plan["stages"] = normalized_stages

    current_stage_id = _safe_text(raw_plan.get("current_stage_id"), limit=80)
    valid_ids = {str(item.get("id") or "") for item in plan["stages"]}
    if current_stage_id and current_stage_id in valid_ids:
        plan["current_stage_id"] = current_stage_id
    else:
        current = get_current_stage(plan)
        plan["current_stage_id"] = _safe_text(
            (current or {}).get("id") or plan["stages"][0]["id"], limit=80
        )
    return plan


def get_current_stage(plan: StagePlan) -> Dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    current_stage_id = _safe_text(plan.get("current_stage_id"), limit=80)
    stages = list(plan.get("stages") or [])
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        if _safe_text(stage.get("id"), limit=80) == current_stage_id:
            return stage
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        if _safe_text(stage.get("status"), limit=40).lower() not in {"completed", "done"}:
            return stage
    return stages[-1] if stages else None


def get_stage_position(plan: StagePlan, stage_id: str) -> tuple[int, int]:
    stages = [dict(item) for item in list(plan.get("stages") or []) if isinstance(item, dict)]
    safe_stage_id = _safe_text(stage_id, limit=80)
    for index, stage in enumerate(stages, start=1):
        if _safe_text(stage.get("id"), limit=80) == safe_stage_id:
            return index, len(stages)
    return 0, len(stages)


def mark_stage_running(plan: StagePlan, *, stage_id: str) -> StagePlan:
    updated = deepcopy(plan)
    safe_stage_id = _safe_text(stage_id, limit=80)
    updated["attempt_count"] = max(0, int(updated.get("attempt_count") or 0)) + 1
    updated["blocked_stage_id"] = ""
    updated["current_stage_id"] = safe_stage_id or _safe_text(updated.get("current_stage_id"), limit=80)
    stages = []
    for row in list(updated.get("stages") or []):
        if not isinstance(row, dict):
            continue
        stage = dict(row)
        if _safe_text(stage.get("id"), limit=80) == safe_stage_id:
            stage["status"] = "running"
            stage["attempt_count"] = max(0, int(stage.get("attempt_count") or 0)) + 1
        stages.append(stage)
    updated["stages"] = stages
    return updated


def mark_stage_completed(
    plan: StagePlan,
    *,
    stage_id: str,
    summary: str,
    output: str,
) -> StagePlan:
    updated = deepcopy(plan)
    safe_stage_id = _safe_text(stage_id, limit=80)
    completed = {
        _safe_text(item, limit=80)
        for item in list(updated.get("completed_stage_ids") or [])
        if _safe_text(item, limit=80)
    }
    if safe_stage_id:
        completed.add(safe_stage_id)
    updated["completed_stage_ids"] = [item for item in completed if item]
    updated["blocked_stage_id"] = ""
    updated["last_stage_summary"] = _safe_text(summary, limit=1000)
    updated["last_stage_output"] = _safe_text(output, limit=4000)

    stages = []
    next_stage_id = ""
    current_found = False
    for row in list(updated.get("stages") or []):
        if not isinstance(row, dict):
            continue
        stage = dict(row)
        safe_id = _safe_text(stage.get("id"), limit=80)
        if safe_id == safe_stage_id:
            stage["status"] = "completed"
            stage["last_summary"] = _safe_text(summary, limit=1000)
            stage["last_output"] = _safe_text(output, limit=4000)
            stage["last_error"] = ""
            current_found = True
        elif current_found and not next_stage_id:
            next_stage_id = safe_id
        stages.append(stage)
    updated["stages"] = stages
    updated["current_stage_id"] = next_stage_id or safe_stage_id
    return updated


def mark_stage_blocked(
    plan: StagePlan,
    *,
    stage_id: str,
    summary: str,
    error: str,
) -> StagePlan:
    updated = deepcopy(plan)
    safe_stage_id = _safe_text(stage_id, limit=80)
    updated["blocked_stage_id"] = safe_stage_id
    updated["last_stage_summary"] = _safe_text(summary, limit=1000)
    updated["last_stage_output"] = _safe_text(error or summary, limit=4000)
    stages = []
    for row in list(updated.get("stages") or []):
        if not isinstance(row, dict):
            continue
        stage = dict(row)
        if _safe_text(stage.get("id"), limit=80) == safe_stage_id:
            stage["status"] = "blocked"
            stage["last_summary"] = _safe_text(summary, limit=1000)
            stage["last_error"] = _safe_text(error, limit=1000)
        stages.append(stage)
    updated["stages"] = stages
    updated["current_stage_id"] = safe_stage_id or _safe_text(updated.get("current_stage_id"), limit=80)
    return updated


def get_next_stage(plan: StagePlan) -> Dict[str, Any] | None:
    current = get_current_stage(plan)
    if current is None:
        return None
    current_id = _safe_text(current.get("id"), limit=80)
    stages = [dict(item) for item in list(plan.get("stages") or []) if isinstance(item, dict)]
    found_current = False
    for stage in stages:
        safe_id = _safe_text(stage.get("id"), limit=80)
        if safe_id == current_id:
            found_current = True
            continue
        if found_current and _safe_text(stage.get("status"), limit=40).lower() not in {
            "completed",
            "done",
        }:
            return stage
    return None


def add_adjustment(plan: StagePlan, *, message: str, source: str) -> StagePlan:
    updated = deepcopy(plan)
    adjustments = [
        dict(item)
        for item in list(updated.get("adjustments") or [])
        if isinstance(item, dict)
    ]
    text = _safe_text(message, limit=1000)
    if text:
        adjustments.append({"source": _safe_text(source, limit=80), "message": text})
    updated["adjustments"] = adjustments[-20:]
    return updated


def merge_collected_files(
    plan: StagePlan,
    *,
    files: List[Dict[str, Any]],
) -> StagePlan:
    updated = deepcopy(plan)
    rows = [
        dict(item)
        for item in list(updated.get("collected_files") or [])
        if isinstance(item, dict)
    ]
    seen = {
        (
            _safe_text(item.get("path"), limit=400),
            _safe_text(item.get("filename"), limit=200),
            _safe_text(item.get("kind"), limit=80),
        )
        for item in rows
    }
    for item in files or []:
        if not isinstance(item, dict):
            continue
        key = (
            _safe_text(item.get("path"), limit=400),
            _safe_text(item.get("filename"), limit=200),
            _safe_text(item.get("kind"), limit=80),
        )
        if key in seen:
            continue
        rows.append(dict(item))
        seen.add(key)
    updated["collected_files"] = rows[-40:]
    return updated


def count_adjustments(plan: StagePlan) -> int:
    return len(
        [item for item in list(plan.get("adjustments") or []) if isinstance(item, dict)]
    )


def build_stage_instruction(
    *,
    original_request: str,
    plan: StagePlan,
    stage: Dict[str, Any],
    resolved_task_goal: str = "",
    previous_summary: str = "",
    previous_output: str = "",
    last_blocking_reason: str = "",
) -> str:
    safe_request = _safe_text(resolved_task_goal or original_request, limit=5000)
    safe_summary = _safe_text(previous_summary, limit=1200)
    safe_output = _safe_text(previous_output, limit=2400)
    safe_blocking = _safe_text(last_blocking_reason, limit=1200)
    stage_id = _safe_text(stage.get("id"), limit=80)
    position, total = get_stage_position(plan, stage_id)
    stage_goal = _safe_text(stage.get("goal"), limit=4000) or safe_request
    if max(1, total) == 1 and resolved_task_goal:
        stage_goal = safe_request

    completed_rows = []
    for row in list(plan.get("stages") or []):
        if not isinstance(row, dict):
            continue
        if _safe_text(row.get("status"), limit=40).lower() != "completed":
            continue
        title = _safe_text(row.get("title"), limit=120)
        summary = _safe_text(row.get("last_summary"), limit=200)
        if title or summary:
            completed_rows.append(f"- {title or row.get('id')}: {summary or '已完成'}")
    adjustments = [
        _safe_text(item.get("message"), limit=240)
        for item in list(plan.get("adjustments") or [])
        if isinstance(item, dict)
    ]

    lines = [
        f"你正在执行用户任务的阶段 {position}/{max(1, total)}。",
        f"原始用户任务：{safe_request or '未提供'}",
        f"当前阶段：{_safe_text(stage.get('title'), limit=200) or stage_id or '未命名阶段'}",
        f"阶段目标：{stage_goal}",
        f"成功标准：{_safe_text(stage.get('success_signal'), limit=400) or '完成当前阶段目标并给出可交付摘要。'}",
    ]
    if completed_rows:
        lines.append("已完成阶段：")
        lines.extend(completed_rows[-4:])
    if safe_summary:
        lines.append(f"上一阶段摘要：{safe_summary}")
    if safe_output:
        lines.append("上一阶段关键输出：")
        lines.append(safe_output)
    if safe_blocking:
        lines.append(f"上一次阻塞原因：{safe_blocking}")
    if adjustments:
        lines.append("用户最新补充/修正：")
        lines.extend([f"- {item}" for item in adjustments[-4:]])
    if max(1, total) > 1:
        if position < total:
            lines.append(
                "注意：本次只完成当前阶段，不要假装整个任务已经全部完成；如果缺少条件无法继续，请明确说明阻塞点。"
            )
        else:
            lines.append(
                "注意：这是最后阶段，默认目标是收口并直接产出最终可交付结果。"
                "不要只重复“还不能交付”的验证说明；若发现信息缺口，优先继续调用可用工具补齐并整理正式输出。"
                "只有在工具预算已耗尽或客观条件确实不足时，才说明阻塞点和缺失项。"
            )
    return "\n\n".join([item for item in lines if str(item).strip()]).strip()
