import json
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
    assert state["status"]["user_id"] == "u1"
    assert store.heartbeat_path("u1").exists()
    assert store.status_path("u1").exists()
    assert store.heartbeat_path("u1").parent == tmp_path.resolve()
    assert store.heartbeat_path("u1").name == "HEARTBEAT.u1.md"


@pytest.mark.asyncio
async def test_heartbeat_store_keeps_users_separate(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    await store.add_checklist_item("u1", "alpha item")
    await store.add_checklist_item("u2", "beta item")
    await store.set_delivery_target("u1", "telegram", "111")
    await store.set_delivery_target("u2", "telegram", "222")

    state_u1 = await store.get_state("u1")
    state_u2 = await store.get_state("u2")

    assert state_u1["checklist"] == ["alpha item"]
    assert state_u2["checklist"] == ["beta item"]
    assert state_u1["status"]["delivery"]["last_chat_id"] == "111"
    assert state_u2["status"]["delivery"]["last_chat_id"] == "222"
    assert store.heartbeat_path("u1") != store.heartbeat_path("u2")


@pytest.mark.asyncio
async def test_heartbeat_store_reads_legacy_shared_root_by_user_id(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    shared_dir = store.root / "user"
    shared_dir.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    shared_text = """---
version: 2
user_id: "user"
checklist_by_user:
  u1:
    - alpha item
  u2:
    - beta item
---

# HEARTBEAT
"""
    (shared_dir / "HEARTBEAT.md").write_text(shared_text, encoding="utf-8")
    (shared_dir / "STATUS.json").write_text(
        json.dumps(
            {
                "version": 2,
                "user_id": "user",
                "delivery_by_user": {
                    "u1": {"last_platform": "telegram", "last_chat_id": "111"},
                    "u2": {"last_platform": "telegram", "last_chat_id": "222"},
                },
                "session_by_user": {
                    "u1": {
                        "active_task": {
                            "id": "task-u1",
                            "goal": "alpha",
                            "status": "running",
                            "source": "message",
                        }
                    },
                    "u2": {
                        "active_task": {
                            "id": "task-u2",
                            "goal": "beta",
                            "status": "running",
                            "source": "message",
                        }
                    },
                },
                "heartbeat_by_user": {
                    "u1": {"last_result": "HEARTBEAT_OK"},
                    "u2": {"last_result": "请尽快修复配置异常。"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    state_u1 = await store.get_state("u1")
    state_u2 = await store.get_state("u2")

    assert state_u1["spec"]["user_id"] == "u1"
    assert state_u2["spec"]["user_id"] == "u2"
    assert state_u1["checklist"] == ["alpha item"]
    assert state_u2["checklist"] == ["beta item"]
    assert state_u1["status"]["delivery"]["last_chat_id"] == "111"
    assert state_u2["status"]["delivery"]["last_chat_id"] == "222"
    assert state_u1["status"]["session"]["active_task"]["id"] == "task-u1"
    assert state_u2["status"]["session"]["active_task"]["id"] == "task-u2"


@pytest.mark.asyncio
async def test_heartbeat_store_does_not_share_ambiguous_legacy_root_by_default(
    tmp_path,
):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    shared_dir = store.root / "user"
    shared_dir.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    shared_text = """---
version: 2
user_id: "user"
target: last
---

# Heartbeat checklist

- shared checklist item
"""
    (shared_dir / "HEARTBEAT.md").write_text(shared_text, encoding="utf-8")
    (shared_dir / "STATUS.json").write_text(
        json.dumps(
            {
                "version": 2,
                "user_id": "user",
                "delivery": {"last_platform": "telegram", "last_chat_id": "999"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    state_u1 = await store.get_state("u1")
    state_u2 = await store.get_state("u2")

    assert state_u1["spec"]["user_id"] == "u1"
    assert state_u2["spec"]["user_id"] == "u2"
    assert state_u1["checklist"] == []
    assert state_u2["checklist"] == []
    assert state_u1["status"]["delivery"]["last_chat_id"] == ""
    assert state_u2["status"]["delivery"]["last_chat_id"] == ""


@pytest.mark.asyncio
async def test_heartbeat_store_keeps_explicit_legacy_shared_owner_readable(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    shared_dir = store.root / "user"
    shared_dir.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    shared_text = """---
version: 2
user_id: "u1"
target: last
---

# Heartbeat checklist

- legacy owned checklist
"""
    (shared_dir / "HEARTBEAT.md").write_text(shared_text, encoding="utf-8")
    (shared_dir / "STATUS.json").write_text(
        json.dumps(
            {
                "version": 2,
                "user_id": "u1",
                "delivery": {"last_platform": "telegram", "last_chat_id": "111"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    state_u1 = await store.get_state("u1")
    state_u2 = await store.get_state("u2")

    assert state_u1["spec"]["user_id"] == "u1"
    assert state_u1["checklist"] == ["legacy owned checklist"]
    assert state_u1["status"]["delivery"]["last_chat_id"] == "111"
    assert state_u2["spec"]["user_id"] == "u2"
    assert state_u2["checklist"] == []
    assert state_u2["status"]["delivery"]["last_chat_id"] == ""


@pytest.mark.asyncio
async def test_heartbeat_store_does_not_copy_ambiguous_shared_status_into_owned_users(
    tmp_path,
):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    shared_dir = store.root / "user"
    shared_dir.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    shared_text = """---
version: 2
user_id: "user"
checklist_by_user:
  u1:
    - alpha item
  u2:
    - beta item
---

# HEARTBEAT
"""
    (shared_dir / "HEARTBEAT.md").write_text(shared_text, encoding="utf-8")
    (shared_dir / "STATUS.json").write_text(
        json.dumps(
            {
                "version": 2,
                "user_id": "user",
                "delivery": {"last_platform": "telegram", "last_chat_id": "999"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    state_u1 = await store.get_state("u1")
    state_u2 = await store.get_state("u2")

    assert state_u1["checklist"] == ["alpha item"]
    assert state_u2["checklist"] == ["beta item"]
    assert state_u1["status"]["delivery"]["last_chat_id"] == ""
    assert state_u2["status"]["delivery"]["last_chat_id"] == ""


@pytest.mark.asyncio
async def test_heartbeat_store_list_users_includes_legacy_status_owner(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    shared_dir = store.root / "user"
    shared_dir.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    (shared_dir / "STATUS.json").write_text(
        json.dumps(
            {
                "version": 2,
                "user_id": "u9",
                "delivery": {"last_platform": "telegram", "last_chat_id": "909"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    assert await store.list_users() == ["u9"]


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
    assert state["spec"]["user_id"] == "u2"
    assert state["checklist"] == []
    assert store.backup_legacy_path("u2").exists()
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
    assert state["spec"]["user_id"] == "u2b"
    assert state["checklist"] == []
    assert store.backup_legacy_path("u2b").exists()
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


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["a/b", "../escape", "user"])
async def test_heartbeat_store_sanitizes_user_ids_for_paths(tmp_path, user_id):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    state = await store.get_state(user_id)
    heartbeat_path = store.heartbeat_path(user_id)
    status_path = store.status_path(user_id)

    assert state["spec"]["user_id"] == user_id
    assert state["status"]["user_id"] == user_id
    assert heartbeat_path.parent == tmp_path.resolve()
    assert status_path.parent.parent == store.root
    assert status_path.parent.name != store.shared_dir_name
    assert ".." not in heartbeat_path.name
    assert "/" not in heartbeat_path.name
    if user_id == "user":
        assert heartbeat_path.name == "HEARTBEAT.md"
    else:
        assert heartbeat_path.name.startswith("HEARTBEAT.")


@pytest.mark.asyncio
async def test_heartbeat_store_reads_legacy_non_shared_dir_for_sanitized_user_id(
    tmp_path,
):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    legacy_dir = store.root / "team" / "ops"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "HEARTBEAT.md").write_text(
        """---
version: 2
user_id: "team/ops"
target: thread
---

# Heartbeat checklist

- legacy nested item
""",
        encoding="utf-8",
    )
    (legacy_dir / "STATUS.json").write_text(
        json.dumps(
            {
                "version": 2,
                "user_id": "team/ops",
                "delivery": {"last_platform": "telegram", "last_chat_id": "303"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    state = await store.get_state("team/ops")

    assert state["spec"]["user_id"] == "team/ops"
    assert state["spec"]["target"] == "thread"
    assert state["checklist"] == ["legacy nested item"]
    assert state["status"]["delivery"]["last_chat_id"] == "303"
    assert store.heartbeat_path("team/ops").exists()
    assert store.heartbeat_path("team/ops").parent == tmp_path.resolve()
    assert store.heartbeat_path("team/ops").name.startswith("HEARTBEAT.uid=")


@pytest.mark.asyncio
async def test_heartbeat_store_reserved_user_id_does_not_read_shared_legacy_dir(
    tmp_path,
):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    shared_dir = store.root / "user"
    shared_dir.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    (shared_dir / "HEARTBEAT.md").write_text(
        """---
version: 2
user_id: "user"
target: thread
---

# Heartbeat checklist

- shared legacy item
""",
        encoding="utf-8",
    )
    (shared_dir / "STATUS.json").write_text(
        json.dumps(
            {
                "version": 2,
                "user_id": "user",
                "delivery": {"last_platform": "telegram", "last_chat_id": "909"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    state = await store.get_state("user")

    assert state["spec"]["user_id"] == "user"
    assert state["spec"]["target"] == store.default_target
    assert state["checklist"] == []
    assert state["status"]["delivery"]["last_chat_id"] == ""
    assert store.heartbeat_path("user").name == "HEARTBEAT.md"


@pytest.mark.asyncio
async def test_heartbeat_store_restores_multiple_legacy_non_shared_dirs_independently(
    tmp_path,
):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    legacy_specs = {
        "team/ops": ("thread", "ops item", "303"),
        "team/dev": ("last", "dev item", "404"),
    }
    for user_id, (target, checklist_item, chat_id) in legacy_specs.items():
        legacy_dir = store.root / user_id
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "HEARTBEAT.md").write_text(
            f"""---
version: 2
user_id: \"{user_id}\"
target: {target}
---

# Heartbeat checklist

- {checklist_item}
""",
            encoding="utf-8",
        )
        (legacy_dir / "STATUS.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "user_id": user_id,
                    "delivery": {
                        "last_platform": "telegram",
                        "last_chat_id": chat_id,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    state_ops = await store.get_state("team/ops")
    state_dev = await store.get_state("team/dev")

    assert state_ops["checklist"] == ["ops item"]
    assert state_ops["spec"]["target"] == "thread"
    assert state_ops["status"]["delivery"]["last_chat_id"] == "303"
    assert state_dev["checklist"] == ["dev item"]
    assert state_dev["spec"]["target"] == "last"
    assert state_dev["status"]["delivery"]["last_chat_id"] == "404"


@pytest.mark.asyncio
async def test_heartbeat_store_list_users_does_not_materialize_shared_root_from_legacy_dirs(
    tmp_path,
):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    legacy_dir = store.root / "team" / "ops"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "HEARTBEAT.md").write_text(
        """---
version: 2
user_id: \"team/ops\"
target: thread
---

# Heartbeat checklist

- legacy nested item
""",
        encoding="utf-8",
    )

    assert await store.list_users() == ["team/ops"]
    assert not (store.root / "user" / "HEARTBEAT.md").exists()
    assert not (store.root / "user" / "STATUS.json").exists()


@pytest.mark.asyncio
async def test_heartbeat_store_list_users_keeps_nested_user_segment_legacy_ids(
    tmp_path,
):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    legacy_dir = store.root / "team" / "user" / "ops"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "HEARTBEAT.md").write_text(
        """---
version: 2
user_id: \"team/user/ops\"
target: thread
---

# Heartbeat checklist

- legacy nested item
""",
        encoding="utf-8",
    )

    users = await store.list_users()

    assert users == ["team/user/ops"]


@pytest.mark.asyncio
async def test_heartbeat_store_list_users_keeps_logical_user_named_user(tmp_path):
    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    await store.add_checklist_item("user", "reserved logical user")

    assert await store.list_users() == ["user"]
