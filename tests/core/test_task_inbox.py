from pathlib import Path

import pytest

from core.task_inbox import TaskInbox


def _build_isolated_inbox(tmp_path: Path) -> TaskInbox:
    inbox = TaskInbox()
    inbox.root = (tmp_path / "task_inbox").resolve()
    inbox.tasks_root = (inbox.root / "tasks").resolve()
    inbox.events_path = (inbox.root / "events.jsonl").resolve()
    inbox.root.mkdir(parents=True, exist_ok=True)
    inbox.tasks_root.mkdir(parents=True, exist_ok=True)
    inbox.events_path.write_text("", encoding="utf-8")
    inbox._loaded = True
    inbox._tasks = {}
    return inbox


@pytest.mark.asyncio
async def test_task_inbox_submit_assign_complete(tmp_path):
    inbox = _build_isolated_inbox(tmp_path)

    task = await inbox.submit(
        source="user_chat",
        goal="帮我检查这个项目",
        user_id="u-1",
        payload={"platform": "telegram"},
        priority="high",
    )

    assert task.task_id
    assert task.status == "pending"

    ok = await inbox.assign_worker(
        task.task_id,
        worker_id="worker-main",
        reason="needs_execution",
    )
    assert ok is True

    updated = await inbox.get(task.task_id)
    assert updated is not None
    assert updated.status == "running"
    assert updated.assigned_worker_id == "worker-main"

    ok = await inbox.complete(
        task.task_id,
        result={"summary": "done"},
        final_output="执行完成",
    )
    assert ok is True

    done = await inbox.get(task.task_id)
    assert done is not None
    assert done.status == "completed"
    assert done.final_output == "执行完成"
    assert done.output.get("text") == "执行完成"


@pytest.mark.asyncio
async def test_task_inbox_fail_persists_structured_output(tmp_path):
    inbox = _build_isolated_inbox(tmp_path)

    task = await inbox.submit(
        source="heartbeat",
        goal="检查订阅",
        user_id="u-err",
    )

    ok = await inbox.fail(
        task.task_id,
        error="rss fetch failed",
        result={"ui": {"actions": [[{"text": "重试", "callback_data": "retry"}]]}},
    )
    assert ok is True

    failed = await inbox.get(task.task_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.output.get("text") == "rss fetch failed"
    assert failed.output.get("ui", {}).get("actions")


@pytest.mark.asyncio
async def test_task_inbox_list_pending_by_priority(tmp_path):
    inbox = _build_isolated_inbox(tmp_path)

    t1 = await inbox.submit(
        source="cron",
        goal="低优先级任务",
        user_id="u-2",
        priority="low",
    )
    t2 = await inbox.submit(
        source="user_chat",
        goal="高优先级任务",
        user_id="u-2",
        priority="high",
    )

    pending = await inbox.list_pending(user_id="u-2", limit=10)
    assert len(pending) == 2
    assert pending[0].task_id == t2.task_id
    assert pending[1].task_id == t1.task_id

    recent_outputs = await inbox.list_recent_outputs(user_id="u-2", limit=5)
    assert len(recent_outputs) == 2
    assert "output" in recent_outputs[0]
