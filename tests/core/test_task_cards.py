from core.task_cards import (
    build_session_brief_lines,
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
