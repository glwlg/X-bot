from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.heartbeat_store import heartbeat_store
from core.task_inbox import task_inbox
from extension.skills.builtin.task_tracker.scripts.service import task_tracker_service


def _reset_task_inbox(tmp_path: Path) -> None:
    root = (tmp_path / "task_inbox").resolve()
    tasks_root = (root / "tasks").resolve()
    archive_root = (root / "archive").resolve()
    events_path = (root / "events.jsonl").resolve()
    tasks_root.mkdir(parents=True, exist_ok=True)
    archive_root.mkdir(parents=True, exist_ok=True)
    task_inbox.persist = True
    task_inbox.root = root
    task_inbox.tasks_root = tasks_root
    task_inbox.archive_root = archive_root
    task_inbox.events_path = events_path
    task_inbox._loaded = False
    task_inbox._tasks = {}


def _reset_heartbeat_store(tmp_path: Path) -> None:
    root = (tmp_path / "runtime_tasks").resolve()
    root.mkdir(parents=True, exist_ok=True)
    heartbeat_store.root = root
    heartbeat_store._locks.clear()


@pytest.fixture
def _isolated_state(tmp_path: Path):
    _reset_task_inbox(tmp_path)
    _reset_heartbeat_store(tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_task_tracker_lists_due_open_tasks(_isolated_state):
    due = await task_inbox.submit(
        source="user_chat",
        goal="跟进 PR A",
        user_id="u-1",
    )
    later = await task_inbox.submit(
        source="user_chat",
        goal="跟进 PR B",
        user_id="u-1",
    )

    await task_inbox.update_status(
        due.task_id,
        "waiting_external",
        event="followup_waiting",
        metadata={
            "followup": {
                "done_when": "PR merged",
                "next_review_after": "2026-03-13T00:00:00+08:00",
            }
        },
    )
    await task_inbox.update_status(
        later.task_id,
        "waiting_external",
        event="followup_waiting",
        metadata={
            "followup": {
                "done_when": "PR merged",
                "next_review_after": "2099-03-13T00:00:00+08:00",
            }
        },
    )

    result = await task_tracker_service.list_open(
        user_id="u-1", due_only=True, limit=10
    )

    assert result["ok"] is True
    assert [row["task_id"] for row in result["data"]["tasks"]] == [due.task_id]


@pytest.mark.asyncio
async def test_task_tracker_lists_due_open_tasks_beyond_first_fifty(_isolated_state):
    overdue = await task_inbox.submit(
        source="user_chat",
        goal="真正到期的任务",
        user_id="u-many",
    )
    await task_inbox.update_status(
        overdue.task_id,
        "waiting_external",
        event="followup_waiting",
        metadata={
            "followup": {
                "done_when": "merged",
                "next_review_after": "2026-03-13T00:00:00+08:00",
            }
        },
    )

    for index in range(260):
        task = await task_inbox.submit(
            source="user_chat",
            goal=f"later-{index}",
            user_id="u-many",
        )
        await task_inbox.update_status(
            task.task_id,
            "waiting_external",
            event="followup_waiting",
            metadata={
                "followup": {
                    "done_when": "merged",
                    "next_review_after": "2099-03-13T00:00:00+08:00",
                }
            },
        )

    result = await task_tracker_service.list_open(
        user_id="u-many",
        due_only=True,
        limit=5,
    )

    assert result["ok"] is True
    assert overdue.task_id in [row["task_id"] for row in result["data"]["tasks"]]


@pytest.mark.asyncio
async def test_task_tracker_update_marks_waiting_external_and_get_reads_followup(
    _isolated_state,
):
    task = await task_inbox.submit(
        source="user_chat",
        goal="跟进发布",
        user_id="u-2",
        metadata={"original_user_request": "跟进发布"},
    )

    updated = await task_tracker_service.update(
        user_id="u-2",
        task_id=task.task_id,
        status="waiting_external",
        result_summary="PR 已创建，等待合并。",
        done_when="PR merged",
        next_review_after="2026-03-13T15:00:00+08:00",
        refs={"pr_url": "https://github.com/example/repo/pull/1"},
        notes="如果 review 要求修改，先通知用户。",
    )

    assert updated["ok"] is True
    assert updated["data"]["task"]["status"] == "waiting_external"
    assert updated["data"]["task"]["metadata"]["followup"]["done_when"] == "PR merged"

    fetched = await task_tracker_service.get(user_id="u-2", task_id=task.task_id)

    assert fetched["ok"] is True
    assert (
        fetched["data"]["task"]["metadata"]["followup"]["notes"]
        == "如果 review 要求修改，先通知用户。"
    )


@pytest.mark.asyncio
async def test_task_tracker_get_returns_task_scoped_recent_events(_isolated_state):
    primary = await task_inbox.submit(
        source="user_chat",
        goal="跟进主任务",
        user_id="u-events",
    )
    secondary = await task_inbox.submit(
        source="user_chat",
        goal="别的任务",
        user_id="u-events",
    )

    await task_inbox.update_status(
        primary.task_id,
        "running",
        event="step_one",
        detail="first detail",
    )
    await task_inbox.update_status(
        primary.task_id,
        "waiting_external",
        event="step_two",
        detail="second detail",
    )
    await task_inbox.update_status(
        secondary.task_id,
        "running",
        event="other_task_event",
        detail="other detail",
    )

    fetched = await task_tracker_service.get(
        user_id="u-events",
        task_id=primary.task_id,
        event_limit=2,
    )

    assert fetched["ok"] is True
    events = fetched["data"]["task"]["events"]
    assert [item["event"] for item in events] == ["step_one", "step_two"]
    assert all(item["detail"] != "other detail" for item in events)


@pytest.mark.asyncio
async def test_task_tracker_list_open_includes_last_event_summary(_isolated_state):
    task = await task_inbox.submit(
        source="user_chat",
        goal="带事件摘要的任务",
        user_id="u-last-event",
    )
    await task_inbox.update_status(
        task.task_id,
        "waiting_external",
        event="followup_waiting",
        detail="等待 reviewer 回复",
        metadata={
            "followup": {
                "done_when": "merged",
                "next_review_after": "2026-03-13T00:00:00+08:00",
            }
        },
    )

    result = await task_tracker_service.list_open(
        user_id="u-last-event",
        due_only=True,
        limit=5,
    )

    assert result["ok"] is True
    row = result["data"]["tasks"][0]
    assert row["last_event"]["event"] == "followup_waiting"
    assert row["last_event"]["detail"] == "等待 reviewer 回复"


@pytest.mark.asyncio
async def test_task_tracker_announce_text_is_audited_and_deduped(
    monkeypatch,
    _isolated_state,
):
    task = await task_inbox.submit(
        source="user_chat",
        goal="跟进待合并任务",
        user_id="u-3",
    )
    await heartbeat_store.set_delivery_target(
        "u-3",
        "telegram",
        "chat-3",
        session_id="sess-u3",
    )

    sent = []

    async def fake_push_background_text(**kwargs):
        sent.append(dict(kwargs))
        return True

    monkeypatch.setattr(
        "extension.skills.builtin.task_tracker.scripts.service.push_background_text",
        fake_push_background_text,
    )

    first, second = await asyncio.gather(
        task_tracker_service.update(
            user_id="u-3",
            task_id=task.task_id,
            status="running",
            announce_text="我检测到新反馈，开始继续处理。",
            announce_key="review-1",
        ),
        task_tracker_service.update(
            user_id="u-3",
            task_id=task.task_id,
            status="running",
            announce_text="我检测到新反馈，开始继续处理。",
            announce_key="review-1",
        ),
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert len(sent) == 1
    assert sent[0]["record_history"] is True
    assert sent[0]["history_user_id"] == "u-3"
    assert sent[0]["history_session_id"] == "sess-u3"

    stored = await task_inbox.get(task.task_id)
    assert stored is not None
    followup = stored.metadata["followup"]
    assert followup["last_announcement_key"] == "review-1"
    assert followup["last_announcement_at"]


@pytest.mark.asyncio
async def test_task_tracker_observation_update_without_announce_does_not_push(
    monkeypatch,
    _isolated_state,
):
    task = await task_inbox.submit(
        source="user_chat",
        goal="跟进部署检查",
        user_id="u-4",
    )

    sent = []

    async def fake_push_background_text(**kwargs):
        sent.append(dict(kwargs))
        return True

    monkeypatch.setattr(
        "extension.skills.builtin.task_tracker.scripts.service.push_background_text",
        fake_push_background_text,
    )

    result = await task_tracker_service.update(
        user_id="u-4",
        task_id=task.task_id,
        status="waiting_external",
        result_summary="检查完成，暂无新变化。",
        last_observation="暂无新变化",
    )

    assert result["ok"] is True
    assert sent == []


@pytest.mark.asyncio
async def test_task_tracker_metadata_only_update_preserves_active_summary(
    _isolated_state,
):
    task = await task_inbox.submit(
        source="user_chat",
        goal="跟进激活中的任务",
        user_id="u-5",
    )
    await heartbeat_store.set_session_active_task(
        "u-5",
        {
            "id": task.task_id,
            "session_task_id": task.task_id,
            "task_inbox_id": task.task_id,
            "goal": task.goal,
            "status": "waiting_external",
            "result_summary": "保留这条摘要",
            "last_user_visible_summary": "保留这条摘要",
        },
    )

    result = await task_tracker_service.update(
        user_id="u-5",
        task_id=task.task_id,
        status="waiting_external",
        last_observation="暂无外部变化",
    )

    assert result["ok"] is True
    active = await heartbeat_store.get_session_active_task("u-5")
    assert active is not None
    assert active["result_summary"] == "保留这条摘要"


@pytest.mark.asyncio
async def test_task_tracker_completed_update_clears_active_session(_isolated_state):
    task = await task_inbox.submit(
        source="user_chat",
        goal="等合并后完成",
        user_id="u-6",
    )
    await heartbeat_store.set_session_active_task(
        "u-6",
        {
            "id": task.task_id,
            "session_task_id": task.task_id,
            "task_inbox_id": task.task_id,
            "goal": task.goal,
            "status": "waiting_external",
            "result_summary": "等待合并",
        },
    )

    result = await task_tracker_service.update(
        user_id="u-6",
        task_id=task.task_id,
        status="completed",
        result_summary="PR 已合并，任务完成。",
    )

    assert result["ok"] is True
    active = await heartbeat_store.get_session_active_task("u-6")
    assert active is None


@pytest.mark.asyncio
async def test_task_tracker_lists_auto_followup_pr_task_as_open(_isolated_state):
    task = await task_inbox.submit(
        source="user_chat",
        goal="提 PR 后持续跟进",
        user_id="u-pr-open",
    )
    await task_inbox.update_status(
        task.task_id,
        "waiting_external",
        event="auto_followup_waiting",
        detail="waiting for pull request merge",
        metadata={
            "followup": {
                "done_when": "GitHub pull request merged",
                "refs": {"pr_url": "https://github.com/Scenx/fuck-skill/pull/22"},
            }
        },
    )

    result = await task_tracker_service.list_open(
        user_id="u-pr-open",
        due_only=True,
        limit=10,
    )

    assert result["ok"] is True
    assert [row["task_id"] for row in result["data"]["tasks"]] == [task.task_id]


@pytest.mark.asyncio
async def test_task_tracker_list_open_excludes_heartbeat_tasks_by_default(
    _isolated_state,
):
    user_task = await task_inbox.submit(
        source="user_chat",
        goal="用户发起的任务",
        user_id="u-hb-exclude",
    )
    heartbeat_task = await task_inbox.submit(
        source="heartbeat",
        goal="heartbeat 跟进任务",
        user_id="u-hb-exclude",
    )
    await task_inbox.update_status(
        user_task.task_id,
        "waiting_external",
        event="followup_waiting",
        metadata={
            "followup": {
                "done_when": "PR merged",
                "next_review_after": "2026-03-13T00:00:00+08:00",
            }
        },
    )
    await task_inbox.update_status(
        heartbeat_task.task_id,
        "waiting_external",
        event="followup_waiting",
        metadata={
            "followup": {
                "done_when": "PR merged",
                "next_review_after": "2026-03-13T00:00:00+08:00",
            }
        },
    )

    result = await task_tracker_service.list_open(
        user_id="u-hb-exclude", due_only=True, limit=10
    )

    assert result["ok"] is True
    task_ids = [row["task_id"] for row in result["data"]["tasks"]]
    assert user_task.task_id in task_ids
    assert heartbeat_task.task_id not in task_ids
