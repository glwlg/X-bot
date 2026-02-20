from datetime import datetime, timedelta

import pytest

from core.heartbeat_store import HeartbeatStore


@pytest.mark.asyncio
async def test_heartbeat_store_v2_create_and_checklist(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    await store.add_checklist_item("u1", "Check inbox for urgent emails")
    state = await store.get_state("u1")

    assert state["spec"]["version"] == 2
    assert state["spec"]["user_id"] == "u1"
    assert "Check inbox for urgent emails" in state["checklist"]
    assert state["status"]["version"] == 2
    assert store.heartbeat_path("u1").exists()
    assert store.status_path("u1").exists()


@pytest.mark.asyncio
async def test_heartbeat_store_migrates_legacy_payload(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    user_dir = store.root / "u2"
    user_dir.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    legacy_text = """---
version: 1
user_id: "u2"
active_task_id: "t1"
tasks:
  - id: "t1"
    goal: "legacy task"
    status: "failed"
---

# HEARTBEAT

## Events
- 2026-02-14T17:00:00 | task_state:t1:failed
"""
    (user_dir / "HEARTBEAT.md").write_text(legacy_text, encoding="utf-8")

    state = await store.get_state("u2")

    assert state["spec"]["version"] == 2
    assert state["checklist"] == []
    assert (user_dir / "HEARTBEAT.v1.bak.md").exists()
    notes = state["status"].get("migration_notes") or []
    assert notes


@pytest.mark.asyncio
async def test_heartbeat_store_migrates_legacy_tasks_even_if_version_two(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    user_dir = store.root / "u2b"
    user_dir.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    legacy_text = """---
version: 2
user_id: "u2b"
tasks:
  - id: "t2"
    goal: "legacy task"
    status: "failed"
---

# HEARTBEAT
## Events
- 2026-02-14T17:00:00 | task_state:t2:failed
"""
    (user_dir / "HEARTBEAT.md").write_text(legacy_text, encoding="utf-8")

    state = await store.get_state("u2b")

    assert state["spec"]["version"] == 2
    assert state["checklist"] == []
    assert (user_dir / "HEARTBEAT.v1.bak.md").exists()
    notes = state["status"].get("migration_notes") or []
    assert notes


@pytest.mark.asyncio
async def test_heartbeat_store_due_and_pause_logic(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    await store.set_heartbeat_spec(
        "u3",
        every="30m",
        active_start="00:00",
        active_end="23:59",
        paused=False,
    )

    old_run = (datetime.now().astimezone() - timedelta(hours=2)).isoformat(timespec="seconds")
    await store.mark_heartbeat_run("u3", "HEARTBEAT_OK", run_at=old_run)
    assert await store.should_run_heartbeat("u3") is True

    now_run = datetime.now().astimezone().isoformat(timespec="seconds")
    await store.mark_heartbeat_run("u3", "HEARTBEAT_OK", run_at=now_run)
    assert await store.should_run_heartbeat("u3") is False

    await store.set_heartbeat_spec("u3", paused=True)
    assert await store.should_run_heartbeat("u3") is False


@pytest.mark.asyncio
async def test_heartbeat_store_delivery_and_session_state(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    await store.set_delivery_target("u4", "telegram", "12345")
    delivery = await store.get_delivery_target("u4")
    assert delivery["platform"] == "telegram"
    assert delivery["chat_id"] == "12345"

    await store.set_session_active_task(
        "u4",
        {
            "id": "task-1",
            "goal": "do something",
            "status": "running",
            "source": "message",
        },
    )
    active = await store.get_session_active_task("u4")
    assert active is not None
    assert active["status"] == "running"

    await store.update_session_active_task("u4", status="done", result_summary="ok")
    active_after_done = await store.get_session_active_task("u4")
    assert active_after_done is None


def test_heartbeat_result_level_classification():
    store = HeartbeatStore()
    assert store.classify_result("HEARTBEAT_OK") == "OK"
    assert store.classify_result("请尽快修复配置异常。") == "ACTION"
    assert store.classify_result("提醒：建议关注最近变更。") == "NOTICE"
