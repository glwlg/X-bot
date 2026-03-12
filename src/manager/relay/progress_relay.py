from __future__ import annotations

import re
from typing import Any, Dict

from shared.contracts.dispatch import TaskEnvelope

PROGRESS_EVENTS_TO_DELIVER = frozenset(
    {
        "tool_call_started",
        "tool_call_finished",
        "retry_after_failure",
        "max_turn_limit",
        "loop_guard",
    }
)

_RAW_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((?:https?://|/)[^)]+\)")
_VERBOSE_PROGRESS_MARKERS = (
    "## ",
    "```",
    "http://",
    "https://",
    "【搜索结果摘要】",
    "当前：",
    "今天（",
    "明天（",
    "天气情况",
    "图片已生成",
    "保存路径",
    "文件路径",
    "{'ok': true",
    "{'ok': True",
)


def format_progress_summary(tool_name: str, summary: str, *, ok: bool) -> str:
    raw = str(summary or "").strip()
    if not raw:
        return ""

    normalized_tool = str(tool_name or "").strip().lower()
    if normalized_tool.startswith("ext_"):
        normalized_tool = normalized_tool[4:]

    if normalized_tool == "load_skill":
        return "技能已加载，正在继续执行。"

    if not ok:
        first_line = raw.splitlines()[0].strip()
        return first_line[:160]

    if (
        "\n" in raw
        or len(raw) > 120
        or any(marker in raw for marker in _VERBOSE_PROGRESS_MARKERS)
        or _RAW_MARKDOWN_LINK_RE.search(raw) is not None
    ):
        if normalized_tool == "bash":
            return "已获得结果，正在整理最终回复。"
        return "已完成当前步骤，正在继续处理。"

    return raw[:160]


def humanize_tool_name(tool_name: str) -> str:
    raw = str(tool_name or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith("ext_"):
        raw = raw[4:]
    alias = {
        "web_search": "搜索",
        "web_browser": "网页浏览",
        "rss_subscribe": "RSS 订阅",
        "stock_watch": "股票行情",
        "reminder": "提醒",
        "deployment_manager": "部署",
        "web_extractor": "网页提取",
        "load_skill": "加载技能",
        "daily_query": "日常查询",
        "bash": "Shell",
        "read": "读取文件",
        "write": "写入文件",
        "edit": "编辑文件",
    }
    if raw in alias:
        return alias[raw]
    return raw.replace("_", " ")


def build_progress_text(task: TaskEnvelope, progress: Dict[str, Any]) -> str:
    progress_obj = dict(progress or {})
    event = str(progress_obj.get("event") or "").strip().lower()
    worker_name = str(task.metadata.get("worker_name") or task.worker_id or "执行助手")
    visible_task_id = (
        str(task.metadata.get("user_visible_task_id") or "").strip()
        or str(task.metadata.get("session_task_id") or "").strip()
        or str(task.metadata.get("task_inbox_id") or "").strip()
        or str(task.task_id or "").strip()
    )
    stage_index = max(0, int(task.metadata.get("stage_index") or 0))
    stage_total = max(0, int(task.metadata.get("stage_total") or 0))
    stage_title = str(task.metadata.get("stage_title") or "").strip()
    turn = max(0, int(progress_obj.get("turn") or 0))
    recent_steps = [
        dict(item)
        for item in list(progress_obj.get("recent_steps") or [])
        if isinstance(item, dict)
    ]
    latest_step = recent_steps[-1] if recent_steps else {}
    tool_name = humanize_tool_name(
        str(latest_step.get("name") or progress_obj.get("running_tool") or "").strip()
    )
    raw_summary = str(latest_step.get("summary") or "").strip()
    latest_status = str(latest_step.get("status") or "").strip().lower()
    summary = format_progress_summary(
        str(latest_step.get("name") or progress_obj.get("running_tool") or "").strip(),
        raw_summary,
        ok=latest_status != "failed",
    )
    failed_tools = [
        humanize_tool_name(str(item).strip())
        for item in list(progress_obj.get("failed_tools") or [])
        if str(item).strip()
    ]

    lines = [f"⏳ {worker_name} 正在处理任务", f"任务ID：`{visible_task_id}`"]
    if stage_index > 0 and stage_total > 0:
        stage_line = f"阶段：{stage_index}/{max(1, stage_total)}"
        if stage_title:
            stage_line += f" - {stage_title}"
        lines.append(stage_line)
    if turn > 0:
        lines.append(f"回合：{turn}")

    if event == "tool_call_started":
        lines.append(f"动作：开始执行 `{tool_name or '工具'}`")
    elif event == "tool_call_finished":
        if latest_status == "failed":
            lines.append(f"动作：`{tool_name or '工具'}` 执行失败")
        else:
            lines.append(f"动作：`{tool_name or '工具'}` 执行完成")
    elif event == "retry_after_failure":
        lines.append("动作：工具失败后自动重试")
    elif event == "max_turn_limit":
        lines.append("动作：工具回合达到上限，准备结束当前尝试")
    elif event == "loop_guard":
        lines.append("动作：检测到重复调用，已触发循环保护")
    else:
        lines.append("动作：执行中")

    if summary:
        lines.append(f"摘要：{summary[:160]}")
    elif failed_tools:
        lines.append("最近失败：" + "，".join(failed_tools[-3:]))

    return "\n".join(lines).strip()
