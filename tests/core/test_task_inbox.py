from pathlib import Path

import pytest

from core.state_store import get_session_entries, save_message
from core.task_inbox import TaskInbox
import core.task_inbox as task_inbox_module


def _build_isolated_inbox(tmp_path: Path) -> TaskInbox:
    inbox = TaskInbox()
    inbox.root = (tmp_path / "task_inbox").resolve()
    inbox.tasks_root = (inbox.root / "tasks").resolve()
    inbox.archive_root = (inbox.root / "archive").resolve()
    inbox.events_path = (inbox.root / "events.jsonl").resolve()
    inbox.root.mkdir(parents=True, exist_ok=True)
    inbox.tasks_root.mkdir(parents=True, exist_ok=True)
    inbox.archive_root.mkdir(parents=True, exist_ok=True)
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

    ok = await inbox.assign_executor(
        task.task_id,
        executor_id="subagent-main",
        reason="needs_execution",
    )
    assert ok is True

    updated = await inbox.get(task.task_id)
    assert updated is not None
    assert updated.status == "running"
    assert updated.executor_id == "subagent-main"

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
async def test_task_inbox_fail_persists_structured_output_for_user_task(tmp_path):
    inbox = _build_isolated_inbox(tmp_path)

    task = await inbox.submit(
        source="user_chat",
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


@pytest.mark.asyncio
async def test_task_inbox_keeps_waiting_external_open_and_merges_metadata(tmp_path):
    inbox = _build_isolated_inbox(tmp_path)

    task = await inbox.submit(
        source="user_chat",
        goal="跟进未合并的 PR",
        user_id="u-open",
        metadata={"session_task_id": "sess-1", "original_user_request": "跟进 PR"},
    )

    ok = await inbox.update_status(
        task.task_id,
        "waiting_external",
        event="followup_waiting",
        detail="等待外部变化",
        metadata={
            "followup": {
                "done_when": "PR merged",
                "next_review_after": "2026-03-13T15:00:00+08:00",
            }
        },
    )

    assert ok is True

    stored = await inbox.get(task.task_id)
    assert stored is not None
    assert stored.status == "waiting_external"
    assert stored.metadata["session_task_id"] == "sess-1"
    assert stored.metadata["followup"]["done_when"] == "PR merged"

    open_rows = await inbox.list_open(user_id="u-open", limit=10)
    assert [row.task_id for row in open_rows] == [task.task_id]


@pytest.mark.asyncio
async def test_task_inbox_merges_result_and_output_dicts(tmp_path):
    inbox = _build_isolated_inbox(tmp_path)

    task = await inbox.submit(
        source="user_chat",
        goal="保留结构化结果",
        user_id="u-merge",
    )

    ok = await inbox.update_status(
        task.task_id,
        "running",
        result={"summary": "first", "payload": {"mode": "a"}},
        output={"text": "first", "ui": {"actions": [[{"text": "A"}]]}},
    )
    assert ok is True

    ok = await inbox.update_status(
        task.task_id,
        "waiting_external",
        result={"payload": {"extra": "b"}},
        output={"ui": {"notice": "kept"}},
    )
    assert ok is True

    stored = await inbox.get(task.task_id)
    assert stored is not None
    assert stored.result["summary"] == "first"
    assert stored.result["payload"]["mode"] == "a"
    assert stored.result["payload"]["extra"] == "b"
    assert stored.output["text"] == "first"
    assert stored.output["ui"]["actions"][0][0]["text"] == "A"
    assert stored.output["ui"]["notice"] == "kept"


def test_task_inbox_defaults_to_persistent(monkeypatch, tmp_path):
    monkeypatch.setattr(task_inbox_module, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TASK_INBOX_PERSIST", raising=False)
    monkeypatch.delenv("TASK_INBOX_CLEAN_ON_START", raising=False)
    monkeypatch.delenv("TASK_INBOX_GLOBAL_EVENT_LOG_ENABLED", raising=False)

    inbox = TaskInbox()

    assert inbox.persist is True
    assert inbox.root.exists()
    assert inbox.tasks_root.exists()
    assert inbox.archive_root.exists()
    assert inbox.events_path.exists() is False


def test_task_inbox_non_persist_mode_does_not_cleanup_existing_root(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(task_inbox_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TASK_INBOX_PERSIST", "false")
    monkeypatch.delenv("TASK_INBOX_CLEAN_ON_START", raising=False)

    root = (tmp_path / "task_inbox").resolve()
    root.mkdir(parents=True, exist_ok=True)
    sentinel = (root / "keep.txt").resolve()
    sentinel.write_text("keep", encoding="utf-8")

    inbox = TaskInbox()

    assert inbox.persist is False
    assert sentinel.exists()


@pytest.mark.asyncio
async def test_task_inbox_complete_syncs_completed_user_chat_to_session(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    inbox = _build_isolated_inbox(tmp_path)

    task = await inbox.submit(
        source="user_chat",
        goal="好了吗",
        user_id="u-sync",
        payload={"session_id": "sess-sync-1"},
        metadata={"session_id": "sess-sync-1"},
    )

    ok = await inbox.complete(
        task.task_id,
        result={"summary": "服务已经恢复正常"},
        final_output="服务已经恢复正常",
    )

    assert ok is True
    rows = await get_session_entries("u-sync", "sess-sync-1")
    assert rows == [{"role": "model", "content": "服务已经恢复正常"}]


@pytest.mark.asyncio
async def test_task_inbox_complete_does_not_duplicate_existing_session_reply(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    inbox = _build_isolated_inbox(tmp_path)
    await save_message("u-sync", "model", "服务已经恢复正常", "sess-sync-2")

    task = await inbox.submit(
        source="user_chat",
        goal="好了吗",
        user_id="u-sync",
        payload={"session_id": "sess-sync-2"},
        metadata={"session_id": "sess-sync-2"},
    )

    ok = await inbox.complete(
        task.task_id,
        result={"summary": "服务已经恢复正常"},
        final_output="服务已经恢复正常",
    )

    assert ok is True
    rows = await get_session_entries("u-sync", "sess-sync-2")
    assert rows == [{"role": "model", "content": "服务已经恢复正常"}]


@pytest.mark.asyncio
async def test_task_inbox_keeps_only_latest_terminal_tasks(tmp_path):
    inbox = _build_isolated_inbox(tmp_path)
    kept_ids = []

    for index in range(12):
        task = await inbox.submit(
            source="user_chat",
            goal=f"task-{index}",
            user_id="u-keep",
        )
        await inbox.complete(
            task.task_id,
            result={"summary": f"done-{index}"},
            final_output=f"done-{index}",
        )
        kept_ids.append(task.task_id)

    rows = await inbox.list_recent(user_id="u-keep", limit=20)

    assert [row.task_id for row in rows] == kept_ids[-10:][::-1]
    assert inbox._task_path(kept_ids[0]).exists() is False
    assert inbox._task_path(kept_ids[1]).exists() is False


@pytest.mark.asyncio
async def test_task_inbox_keeps_resume_window_completed_task_beyond_limit(tmp_path):
    inbox = _build_isolated_inbox(tmp_path)

    pinned = await inbox.submit(
        source="user_chat",
        goal="resume-me",
        user_id="u-pin",
    )
    await inbox.update_status(
        pinned.task_id,
        "completed",
        event="session_completed",
        metadata={"resume_window_until": "2099-03-13T00:00:00+08:00"},
        result={"summary": "resume"},
        final_output="resume",
        output={"text": "resume"},
    )

    for index in range(12):
        task = await inbox.submit(
            source="user_chat",
            goal=f"other-{index}",
            user_id="u-pin",
        )
        await inbox.complete(
            task.task_id,
            result={"summary": f"done-{index}"},
            final_output=f"done-{index}",
        )

    rows = await inbox.list_recent(user_id="u-pin", limit=30)

    assert any(row.task_id == pinned.task_id for row in rows)
    assert inbox._task_path(pinned.task_id).exists()


@pytest.mark.asyncio
async def test_task_inbox_deletes_terminal_heartbeat_tasks(tmp_path):
    inbox = _build_isolated_inbox(tmp_path)

    task = await inbox.submit(
        source="heartbeat",
        goal="检查状态",
        user_id="u-hb",
    )
    ok = await inbox.complete(
        task.task_id,
        result={"summary": "done"},
        final_output="done",
    )

    assert ok is True
    assert await inbox.get(task.task_id) is None
    assert inbox._task_path(task.task_id).exists() is False


@pytest.mark.asyncio
async def test_task_inbox_delete_removes_task_file(tmp_path):
    inbox = _build_isolated_inbox(tmp_path)

    task = await inbox.submit(
        source="user_chat",
        goal="手动删除的任务",
        user_id="u-delete",
    )

    ok = await inbox.delete(task.task_id)

    assert ok is True
    assert await inbox.get(task.task_id) is None
    assert inbox._task_path(task.task_id).exists() is False


@pytest.mark.asyncio
async def test_task_inbox_caps_task_events_at_fifty(tmp_path):
    inbox = _build_isolated_inbox(tmp_path)

    task = await inbox.submit(
        source="user_chat",
        goal="事件很多",
        user_id="u-events",
    )
    for index in range(60):
        await inbox.update_status(
            task.task_id,
            "running",
            event=f"step-{index}",
            detail=f"detail-{index}",
        )

    stored = await inbox.get(task.task_id)

    assert stored is not None
    assert len(stored.events) == 50
    assert stored.events[0]["event"] == "step-10"
    assert stored.events[-1]["event"] == "step-59"
