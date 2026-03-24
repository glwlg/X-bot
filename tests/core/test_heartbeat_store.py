import json
from datetime import datetime, timedelta

import pytest

from core.heartbeat_store import HeartbeatStore


@pytest.mark.asyncio
async def test_heartbeat_store_creates_single_canonical_state(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    await store.add_checklist_item("u1", "Check inbox for urgent emails")
    state = await store.get_state("u1")

    assert state["spec"]["version"] == 2
    assert state["checklist"] == ["Check inbox for urgent emails"]
    assert state["status"]["version"] == 2
    assert "user_id" not in state["spec"]
    assert "user_id" not in state["status"]
    assert store.heartbeat_path("u1").exists()
    assert store.status_path("u1").exists()
    assert store.heartbeat_path("u1").parent == tmp_path.resolve()
    assert store.heartbeat_path("u1").name == "HEARTBEAT.md"
    assert store.status_path("u1").name == "STATUS.json"


@pytest.mark.asyncio
async def test_heartbeat_store_shares_state_across_runtime_user_ids(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    await store.add_checklist_item("u1", "alpha item")
    await store.set_delivery_target("u1", "telegram", "111", session_id="sess-u1")

    state_u1 = await store.get_state("u1")
    state_u2 = await store.get_state("u2")

    assert state_u1["checklist"] == ["alpha item"]
    assert state_u2["checklist"] == ["alpha item"]
    assert state_u1["status"]["delivery"]["last_chat_id"] == "111"
    assert state_u2["status"]["delivery"]["last_chat_id"] == "111"
    assert state_u1["status"]["delivery"]["last_session_id"] == "sess-u1"
    assert state_u2["status"]["delivery"]["last_session_id"] == "sess-u1"
    assert store.heartbeat_path("u1") == store.heartbeat_path("u2")


@pytest.mark.asyncio
async def test_heartbeat_store_normalizes_and_cleans_legacy_layout(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    (tmp_path / "HEARTBEAT.u1.md").write_text(
        """---
version: 2
target: thread
---

# Heartbeat checklist

- legacy item
""",
        encoding="utf-8",
    )
    legacy_dir = store.root / "u1"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "STATUS.json").write_text(
        json.dumps(
            {
                "version": 2,
                "delivery": {"last_platform": "telegram", "last_chat_id": "111"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    removed = await store.normalize_runtime_tree()
    state = await store.get_state("u1")

    assert removed >= 2
    assert state["spec"]["target"] == "thread"
    assert state["checklist"] == ["legacy item"]
    assert state["status"]["delivery"]["last_chat_id"] == "111"
    assert (tmp_path / "HEARTBEAT.u1.md").exists() is False
    assert legacy_dir.exists() is False
    assert store.heartbeat_path("ignored").exists()
    assert store.status_path("ignored").exists()


@pytest.mark.asyncio
async def test_heartbeat_store_migrates_legacy_payload_and_keeps_note(tmp_path):
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
    assert store.backup_legacy_path("u2").exists()
    assert state["status"]["migration_notes"]


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

    old_run = (datetime.now().astimezone() - timedelta(hours=2)).isoformat(
        timespec="seconds"
    )
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

    await store.set_delivery_target("u4", "telegram", "12345", session_id="sess-4")
    delivery = await store.get_delivery_target("u4")
    assert delivery["platform"] == "telegram"
    assert delivery["chat_id"] == "12345"
    assert delivery["session_id"] == "sess-4"

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


@pytest.mark.asyncio
async def test_heartbeat_store_persists_per_item_delivery_targets(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    await store.set_delivery_target("u5", "telegram", "fallback-chat")
    await store.add_checklist_item(
        "u5",
        "检查微信消息",
        platform="weixin",
        chat_id="wx-user-1",
    )
    await store.add_checklist_item("u5", "检查邮件")
    await store.set_checklist_item_delivery("u5", 2, "telegram", "mail-chat")

    spec = await store.get_heartbeat_spec("u5")

    assert spec["checklist"] == ["检查微信消息", "检查邮件"]
    assert spec["checklist_items"] == [
        {
            "index": 1,
            "text": "检查微信消息",
            "delivery_target": {"platform": "weixin", "chat_id": "wx-user-1"},
        },
        {
            "index": 2,
            "text": "检查邮件",
            "delivery_target": {"platform": "telegram", "chat_id": "mail-chat"},
        },
    ]


def test_heartbeat_result_level_classification():
    store = HeartbeatStore()
    assert store.classify_result("HEARTBEAT_OK") == "OK"
    assert store.classify_result("HEARTBEAT_ACTION: 请尽快修复配置异常。") == "ACTION"
    assert store.classify_result("HEARTBEAT_NOTICE: 建议关注最近变更。") == "NOTICE"
    assert store.classify_result("普通说明") == "NOTICE"
