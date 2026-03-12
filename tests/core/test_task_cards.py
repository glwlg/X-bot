from types import SimpleNamespace

from core.task_cards import (
    build_session_brief_lines,
    format_followup_context,
    format_waiting_user_card,
)


def test_build_session_brief_lines_renders_stage_title():
    lines = build_session_brief_lines(
        session_task_id="tsk-1",
        stage_index=2,
        stage_total=4,
        stage_title="整理交付",
    )

    assert lines == ["任务：`tsk-1`", "阶段：2/4 - 整理交付"]


def test_format_waiting_user_card_keeps_task_oriented_copy():
    text = format_waiting_user_card(
        session_task_id="tsk-2",
        stage_index=1,
        stage_total=3,
        stage_title="收集信息",
        completed_lines=["- 阶段 0: 已完成"],
        blocking_reason="缺少必要输入",
    )

    assert "任务暂时卡住了" in text
    assert "任务：`tsk-2`" in text
    assert "已完成：" in text
    assert "缺少必要输入" in text
    assert "回复“继续”" in text


def test_format_followup_context_includes_stage_brief():
    snapshot = SimpleNamespace(
        session_task_id="tsk-3",
        status="completed",
        stage_index=3,
        stage_total=3,
        stage_title="整理交付",
        original_user_request="帮我关闭 n8n",
        last_user_visible_summary="n8n 已关闭。",
    )

    text = format_followup_context(snapshot)

    assert "最近任务：`tsk-3`" in text
    assert "阶段：3/3 - 整理交付" in text
    assert "原始任务：帮我关闭 n8n" in text
    assert "最近结果摘要：n8n 已关闭。" in text
