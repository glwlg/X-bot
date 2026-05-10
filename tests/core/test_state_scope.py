import json

import pytest

from core.heartbeat_store import HeartbeatStore
from core.state_io import read_json, write_json
from core.state_migration import migrate_legacy_user_state
from core.state_paths import all_user_ids, shared_user_path, system_path, user_path
from extension.skills.builtin.scheduler_manager.scripts.store import (
    add_scheduled_task,
    get_all_active_tasks,
    get_all_scheduled_tasks,
    update_scheduled_task,
    update_task_status,
)
from extension.skills.learned.reminder.scripts.store import (
    add_reminder,
    get_pending_reminders,
)
from extension.skills.learned.rss_subscribe.scripts.store import (
    create_subscription,
    get_rss_delivery_target,
    list_subscriptions,
    set_rss_delivery_target,
)
from extension.skills.learned.stock_watch.scripts.store import (
    add_watchlist_stock,
    get_stock_delivery_target,
    get_user_watchlist,
    set_stock_delivery_target,
)


@pytest.mark.asyncio
async def test_single_user_paths_and_logical_scope_are_canonical(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    alpha = user_path("1001", "scheduler_manager", "scheduled_tasks.md")
    beta = user_path("2002", "scheduler_manager", "scheduled_tasks.md")
    shared = shared_user_path("scheduler_manager", "scheduled_tasks.md")

    assert alpha == beta == shared
    assert alpha == tmp_path / "user" / "scheduler_manager" / "scheduled_tasks.md"
    assert system_path("allowed_users.md") == tmp_path / "system" / "allowed_users.md"
    assert all_user_ids() == ["user"]


@pytest.mark.asyncio
async def test_state_store_rows_are_shared_across_runtime_user_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await create_subscription(
        "1001",
        {"title": "Alpha Feed", "feed_url": "https://example.com/alpha.xml"},
    )
    await add_reminder(
        "1001",
        "chat-1",
        "alpha reminder",
        "2026-03-12T08:00:00+00:00",
    )
    await add_scheduled_task("0 8 * * *", "alpha", user_id="1001")
    await add_watchlist_stock("1001", "AAA", "Alpha")

    assert [row["title"] for row in await list_subscriptions("1001")] == ["Alpha Feed"]
    assert [row["title"] for row in await list_subscriptions("2002")] == ["Alpha Feed"]
    assert [row["message"] for row in await get_pending_reminders("2002")] == [
        "alpha reminder"
    ]
    assert [row["instruction"] for row in await get_all_active_tasks("2002")] == [
        "alpha"
    ]
    assert [row["stock_code"] for row in await get_user_watchlist("2002")] == ["AAA"]


@pytest.mark.asyncio
async def test_paused_scheduled_tasks_remain_listable(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    paused_id = await add_scheduled_task("0 8 * * *", "paused task", user_id="1001")
    active_id = await add_scheduled_task("0 9 * * *", "active task", user_id="1001")

    assert await update_task_status(paused_id, False, user_id="1001") is True

    all_rows = await get_all_scheduled_tasks("2002")
    assert [(row["id"], row["instruction"], row["is_active"]) for row in all_rows] == [
        (paused_id, "paused task", False),
        (active_id, "active task", True),
    ]
    assert [row["id"] for row in await get_all_active_tasks("2002")] == [active_id]


@pytest.mark.asyncio
async def test_feature_delivery_targets_are_shared_across_runtime_user_ids(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await set_rss_delivery_target("1001", "weixin", "wx-user-1")
    await set_stock_delivery_target("1001", "telegram", "257675041")

    assert await get_rss_delivery_target("2002") == {
        "platform": "weixin",
        "chat_id": "wx-user-1",
        "updated_at": (await get_rss_delivery_target("1001"))["updated_at"],
    }
    assert await get_stock_delivery_target("2002") == {
        "platform": "telegram",
        "chat_id": "257675041",
        "updated_at": (await get_stock_delivery_target("1001"))["updated_at"],
    }


@pytest.mark.asyncio
async def test_scheduled_tasks_rewrite_to_single_user_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    scheduled_path = shared_user_path("scheduler_manager", "scheduled_tasks.md")
    await write_json(
        scheduled_path,
        [
            {
                "id": 3,
                "user_id": "",
                "crontab": "10 8 * * *",
                "instruction": "legacy blank owner",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            },
            {
                "id": 3,
                "user_id": "1001",
                "crontab": "10 8 * * *",
                "instruction": "latest alpha",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            },
            {
                "id": 4,
                "user_id": "2002",
                "crontab": "20 9 * * *",
                "instruction": "beta task",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            },
        ],
    )

    rows = await get_all_active_tasks("1001")
    assert [(row["id"], row["instruction"]) for row in rows] == [
        (3, "latest alpha"),
        (4, "beta task"),
    ]

    assert await update_scheduled_task(3, "1001", instruction="alpha updated") is True

    persisted = await read_json(scheduled_path, [])
    assert persisted == [
        {
            "id": 3,
            "crontab": "10 8 * * *",
            "instruction": "alpha updated",
            "platform": "telegram",
            "need_push": True,
            "is_active": True,
            "created_at": persisted[0]["created_at"],
            "updated_at": persisted[0]["updated_at"],
        },
        {
            "id": 4,
            "crontab": "20 9 * * *",
            "instruction": "beta task",
            "platform": "telegram",
            "need_push": True,
            "is_active": True,
            "created_at": persisted[1]["created_at"],
            "updated_at": persisted[1]["updated_at"],
        },
    ]
    assert all("user_id" not in row for row in persisted)


@pytest.mark.asyncio
async def test_heartbeat_store_uses_single_canonical_files(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    await store.add_checklist_item("1001", "alpha heartbeat")
    await store.set_delivery_target("1001", "telegram", "chat-1", session_id="sess-1001")

    state = await store.get_state("2002")

    assert state["checklist"] == ["alpha heartbeat"]
    assert state["status"]["delivery"]["last_chat_id"] == "chat-1"
    assert state["status"]["delivery"]["last_session_id"] == "sess-1001"
    assert "user_id" not in state["spec"]
    assert "user_id" not in state["status"]
    assert store.heartbeat_path("1001") == tmp_path / "HEARTBEAT.md"
    assert store.status_path("1001") == tmp_path / "runtime_tasks" / "STATUS.json"
    assert await store.list_users() == ["user"]


@pytest.mark.asyncio
async def test_heartbeat_store_normalizes_legacy_variants_into_single_files(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    store = HeartbeatStore()
    store.root = (tmp_path / "runtime_tasks").resolve()
    store.root.mkdir(parents=True, exist_ok=True)
    store._locks.clear()

    (tmp_path / "HEARTBEAT.user.md").write_text(
        """---
version: 2
target: last
paused: true
---

# Heartbeat checklist

- keep shared checklist
""",
        encoding="utf-8",
    )
    legacy_dir = store.root / "257675041"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "STATUS.json").write_text(
        json.dumps(
            {
                "version": 2,
                "delivery": {"last_platform": "telegram", "last_chat_id": "chat-1"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    removed = await store.normalize_runtime_tree()
    state = await store.get_state("any-runtime-user")

    assert removed >= 2
    assert state["checklist"] == ["keep shared checklist"]
    assert state["spec"]["paused"] is True
    assert state["status"]["delivery"]["last_chat_id"] == "chat-1"
    assert (tmp_path / "HEARTBEAT.user.md").exists() is False
    assert (store.root / "257675041").exists() is False
    assert store.heartbeat_path("ignored").exists()
    assert store.status_path("ignored").exists()


@pytest.mark.asyncio
async def test_migrate_legacy_user_state_reports_single_user_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    runtime_root = (tmp_path / "runtime_tasks").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    legacy_dir = runtime_root / "u-old"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "STATUS.json").write_text(
        json.dumps({"version": 2, "delivery": {"last_chat_id": "legacy"}}),
        encoding="utf-8",
    )

    report = await migrate_legacy_user_state()
    persisted = await read_json(system_path("state_migrations", "legacy_user_state.md"), {})

    assert report["report_name"] == "legacy_user_state"
    assert report["summary"]["domains"] == [
        "heartbeat",
        "reminders",
        "rss",
        "scheduler",
        "watchlist",
    ]
    assert report["domains"]["heartbeat"]["skipped"] >= 1
    assert persisted["history"][-1]["summary"] == report["summary"]
