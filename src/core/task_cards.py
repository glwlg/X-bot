from __future__ import annotations

from typing import Any


def build_session_brief_lines(
    *,
    session_task_id: str = "",
    stage_index: int = 0,
    stage_total: int = 0,
    stage_title: str = "",
) -> list[str]:
    lines: list[str] = []
    safe_session_task_id = str(session_task_id or "").strip()
    safe_stage_title = str(stage_title or "").strip()
    if safe_session_task_id:
        lines.append(f"任务：`{safe_session_task_id}`")
    if stage_index > 0 and stage_total > 0:
        stage_line = f"阶段：{stage_index}/{max(1, stage_total)}"
        if safe_stage_title:
            stage_line += f" - {safe_stage_title}"
        lines.append(stage_line)
    elif safe_stage_title:
        lines.append(f"阶段：{safe_stage_title}")
    return lines


def format_stage_continue_card(
    *,
    session_task_id: str,
    stage_index: int,
    stage_total: int,
    stage_title: str,
) -> str:
    task_line = f"任务：`{session_task_id}`" if str(session_task_id or "").strip() else "任务继续推进中"
    stage_line = (
        f"阶段 {stage_index}/{max(1, stage_total)}"
        if stage_index > 0 and stage_total > 0
        else "下一阶段"
    )
    title_line = f"：{str(stage_title or '').strip()}" if str(stage_title or "").strip() else ""
    return f"⏳ {task_line}\n\n已完成当前阶段，正在继续 {stage_line}{title_line}。"


def format_waiting_user_card(
    *,
    session_task_id: str,
    stage_index: int,
    stage_total: int,
    stage_title: str,
    completed_lines: list[str],
    blocking_reason: str,
) -> str:
    lines = ["⏸ 任务暂时卡住了，但我还没有结束它。"]
    brief_lines = build_session_brief_lines(
        session_task_id=session_task_id,
        stage_index=stage_index,
        stage_total=stage_total,
        stage_title="",
    )
    if brief_lines:
        lines.extend(["", *brief_lines])
    if completed_lines:
        lines.extend(["", "已完成：", *completed_lines])
    safe_stage_title = str(stage_title or "").strip()
    safe_blocking_reason = str(blocking_reason or "").strip()
    if safe_blocking_reason or safe_stage_title:
        lines.extend(
            [
                "",
                "当前卡点：",
                f"- {safe_blocking_reason or f'在“{safe_stage_title}”阶段暂时未完成收口。'}",
            ]
        )
    lines.extend(
        [
            "",
            "建议下一步：",
            "- 回复“继续”，我会基于现有结果换一种策略继续推进",
            "- 也可以直接补充要求/约束，我会在同一个任务里调整方案",
            "- 如果不需要继续，回复“停止”",
        ]
    )
    return "\n".join(lines).strip()


def format_followup_context(snapshot: Any) -> str:
    session_task_id = str(getattr(snapshot, "session_task_id", "") or "").strip()
    status = str(getattr(snapshot, "status", "") or "").strip() or "completed"
    original_request = str(getattr(snapshot, "original_user_request", "") or "").strip()
    summary = str(getattr(snapshot, "last_user_visible_summary", "") or "").strip()
    stage_index = max(0, int(getattr(snapshot, "stage_index", 0) or 0))
    stage_total = max(0, int(getattr(snapshot, "stage_total", 0) or 0))
    stage_title = str(getattr(snapshot, "stage_title", "") or "").strip()

    context_lines = [
        "【任务续接上下文】",
        f"- 最近任务：`{session_task_id}`",
        f"- 状态：{status}",
    ]
    for line in build_session_brief_lines(
        session_task_id="",
        stage_index=stage_index,
        stage_total=stage_total,
        stage_title=stage_title,
    ):
        context_lines.append(f"- {line}")
    if original_request:
        context_lines.append(f"- 原始任务：{original_request}")
    if summary:
        context_lines.append(f"- 最近结果摘要：{summary}")
    context_lines.append(
        "这句短回复是在承接上一条已完成任务，请优先理解为继续协助，而不是重复解释上一条结果。"
    )
    return "\n".join(context_lines).strip()
