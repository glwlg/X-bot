from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.heartbeat_store import heartbeat_store
from core.session_task_store import session_task_store
from core.task_inbox import task_inbox


def _reset_task_inbox(tmp_path: Path) -> None:
    root = (tmp_path / "task_inbox").resolve()
    tasks_root = (root / "tasks").resolve()
    events_path = (root / "events.jsonl").resolve()
    tasks_root.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    task_inbox.persist = True
    task_inbox.root = root
    task_inbox.tasks_root = tasks_root
    task_inbox.events_path = events_path
    task_inbox._loaded = False
    task_inbox._tasks = {}


def _reset_heartbeat_store(tmp_path: Path) -> None:
    root = (tmp_path / "runtime_tasks").resolve()
    root.mkdir(parents=True, exist_ok=True)
    heartbeat_store.root = root
    heartbeat_store._locks.clear()


@pytest.fixture
def _isolated_state(tmp_path):
    _reset_task_inbox(tmp_path)
    _reset_heartbeat_store(tmp_path)
    return tmp_path


def _future_iso(minutes: int = 15) -> str:
    return (datetime.now().astimezone() + timedelta(minutes=minutes)).isoformat(
        timespec="seconds"
    )


@pytest.mark.asyncio
async def test_match_followup_binds_recent_completed_session(_isolated_state):
    session = await task_inbox.submit(
        source="user_chat",
        goal="帮我关闭 n8n",
        user_id="u-1",
        metadata={
            "session_task_id": "tsk-followup-1",
            "original_user_request": "帮我关闭 n8n",
        },
    )
    await task_inbox.update_status(
        session.task_id,
        "completed",
        event="session_completed",
        detail="done",
        metadata={
            "session_task_id": session.task_id,
            "task_goal": "帮我关闭 n8n",
            "original_user_request": "帮我关闭 n8n",
            "delivery_state": "delivered",
            "resume_window_until": _future_iso(),
            "last_user_visible_summary": "n8n 已关闭，两个容器都已退出。",
        },
        final_output="n8n 已关闭，两个容器都已退出。",
        output={"text": "n8n 已关闭，两个容器都已退出。"},
    )

    matched = await session_task_store.match_followup("u-1", "需要")

    assert matched is not None
    assert matched["session_task_id"] == session.task_id
    assert "n8n 已关闭" in matched["context_text"]
    assert "任务续接上下文" in matched["context_text"]


@pytest.mark.asyncio
async def test_match_followup_ignores_expired_completed_session(_isolated_state):
    session = await task_inbox.submit(
        source="user_chat",
        goal="帮我总结部署结果",
        user_id="u-2",
        metadata={"session_task_id": "tsk-expired-1"},
    )
    expired = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat(
        timespec="seconds"
    )
    await task_inbox.update_status(
        session.task_id,
        "completed",
        event="session_completed",
        detail="done",
        metadata={
            "session_task_id": session.task_id,
            "resume_window_until": expired,
            "last_user_visible_summary": "部署已经完成。",
        },
    )

    matched = await session_task_store.match_followup("u-2", "继续")

    assert matched is None


@pytest.mark.asyncio
async def test_get_active_prefers_heartbeat_session_view(_isolated_state):
    session = await task_inbox.submit(
        source="user_chat",
        goal="帮我修复部署",
        user_id="u-3",
        metadata={
            "session_task_id": "tsk-active-1",
            "stage_id": "stage-2",
            "stage_title": "执行主要任务",
            "stage_index": 2,
            "stage_total": 3,
            "attempt_index": 1,
            "delivery_state": "pending",
            "last_user_visible_summary": "当前卡在重启服务。",
        },
    )
    await heartbeat_store.set_session_active_task(
        "u-3",
        {
            "id": "mgr-active-1",
            "session_task_id": session.task_id,
            "task_inbox_id": session.task_id,
            "goal": session.goal,
            "status": "waiting_user",
            "stage_index": 2,
            "stage_total": 3,
            "stage_id": "stage-2",
            "stage_title": "执行主要任务",
            "attempt_index": 1,
            "delivery_state": "retrying",
            "last_user_visible_summary": "任务暂时卡住了。",
        },
    )

    active = await session_task_store.get_active("u-3")

    assert active is not None
    assert active.status == "waiting_user"
    assert active.session_task_id == session.task_id
    assert active.current_stage_id == "stage-2"
    assert active.delivery_state == "retrying"
    assert "任务暂时卡住了" in active.last_user_visible_summary
