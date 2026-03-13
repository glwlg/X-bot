import json
from unittest.mock import ANY

import pytest

from core.heartbeat_store import HeartbeatStore
from core.state_io import read_json, write_json
from core.state_migration import MIGRATION_REPORT_PATH_PARTS, migrate_legacy_user_state
from core.state_paths import all_user_ids, shared_user_path, system_path, user_path
from core.state_store import (
    add_reminder,
    add_scheduled_task,
    add_watchlist_stock,
    create_subscription,
    delete_reminder,
    delete_subscription,
    delete_task,
    get_all_active_tasks,
    get_pending_reminders,
    get_user_watchlist,
    list_all_subscriptions,
    list_subscriptions,
    remove_watchlist_stock,
)


@pytest.mark.asyncio
async def test_user_path_anchors_user_scoped_data_under_single_user_root(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    alpha = user_path("1001", "automation", "scheduled_tasks.md")
    beta = user_path("2002", "automation", "scheduled_tasks.md")
    shared = shared_user_path("automation", "scheduled_tasks.md")

    assert alpha == beta
    assert alpha == tmp_path / "user" / "automation" / "scheduled_tasks.md"
    assert shared == tmp_path / "user" / "automation" / "scheduled_tasks.md"
    assert all_user_ids() == ["1001", "2002"]


@pytest.mark.asyncio
async def test_system_path_anchors_under_canonical_system_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    target = system_path("allowed_users.md")

    assert target == tmp_path / "system" / "allowed_users.md"


@pytest.mark.asyncio
async def test_state_store_uses_single_user_root_for_current_state(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await create_subscription(
        "1001",
        {"title": "Alpha Feed", "feed_url": "https://example.com/alpha.xml"},
    )
    await add_reminder("1001", "chat-1", "alpha reminder", "2026-03-12T08:00:00+00:00")
    await add_scheduled_task("0 8 * * *", "alpha", user_id="1001")
    await add_watchlist_stock("1001", "AAA", "Alpha")

    heartbeat_store = HeartbeatStore()
    heartbeat_store.root = (tmp_path / "runtime_tasks").resolve()
    heartbeat_store.root.mkdir(parents=True, exist_ok=True)
    heartbeat_store._locks.clear()
    await heartbeat_store.add_checklist_item("1001", "alpha heartbeat")

    alpha_tasks = await get_all_active_tasks("1001")
    assert [row["instruction"] for row in alpha_tasks] == ["alpha"]

    alpha_subs = await list_subscriptions("1001")
    assert [row["title"] for row in alpha_subs] == ["Alpha Feed"]

    alpha_reminders = await get_pending_reminders("1001")
    assert [row["message"] for row in alpha_reminders] == ["alpha reminder"]

    alpha_watchlist = await get_user_watchlist("1001")
    assert [row["stock_code"] for row in alpha_watchlist] == ["AAA"]

    alpha_heartbeat = await heartbeat_store.get_state("1001")
    assert alpha_heartbeat["checklist"] == ["alpha heartbeat"]

    assert shared_user_path("rss", "subscriptions.md").exists()
    assert shared_user_path("automation", "reminders.md").exists()
    assert shared_user_path("automation", "scheduled_tasks.md").exists()
    assert shared_user_path("stock", "watchlist.md").exists()


@pytest.mark.asyncio
async def test_all_user_ids_returns_logical_ids_for_slash_users(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await add_scheduled_task("0 8 * * *", "slash scoped task", user_id="team/ops")

    assert all_user_ids() == ["team/ops"]
    assert await get_all_active_tasks() == [
        {
            "id": 1,
            "user_id": "team/ops",
            "crontab": "0 8 * * *",
            "instruction": "slash scoped task",
            "platform": "telegram",
            "need_push": True,
            "is_active": True,
            "created_at": ANY,
            "updated_at": ANY,
        }
    ]


@pytest.mark.asyncio
async def test_state_store_reads_legacy_shared_state_by_user_id(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await write_json(
        shared_user_path("automation", "scheduled_tasks.md"),
        [
            {
                "id": 1,
                "user_id": "1001",
                "crontab": "0 8 * * *",
                "instruction": "legacy alpha",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            },
            {
                "id": 2,
                "user_id": "2002",
                "crontab": "0 9 * * *",
                "instruction": "legacy beta",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            },
        ],
    )
    await write_json(
        shared_user_path("rss", "subscriptions.md"),
        [
            {
                "id": 11,
                "user_id": "1001",
                "title": "Legacy Alpha Feed",
                "feed_url": "https://example.com/legacy-alpha.xml",
                "provider": "rss",
                "platform": "telegram",
            },
            {
                "id": 22,
                "user_id": "2002",
                "title": "Legacy Beta Feed",
                "feed_url": "https://example.com/legacy-beta.xml",
                "provider": "rss",
                "platform": "telegram",
            },
        ],
    )
    await write_json(
        shared_user_path("automation", "reminders.md"),
        [
            {
                "id": 31,
                "user_id": "1001",
                "chat_id": "chat-1",
                "message": "legacy alpha reminder",
                "trigger_time": "2026-03-12T08:00:00+00:00",
                "platform": "telegram",
            },
            {
                "id": 32,
                "user_id": "2002",
                "chat_id": "chat-2",
                "message": "legacy beta reminder",
                "trigger_time": "2026-03-12T09:00:00+00:00",
                "platform": "telegram",
            },
        ],
    )
    await write_json(
        shared_user_path("stock", "watchlist.md"),
        [
            {"user_id": "1001", "stock_code": "AAA", "stock_name": "Alpha"},
            {"user_id": "2002", "stock_code": "BBB", "stock_name": "Beta"},
        ],
    )

    alpha_tasks = await get_all_active_tasks("1001")
    beta_tasks = await get_all_active_tasks("2002")
    assert [row["instruction"] for row in alpha_tasks] == ["legacy alpha"]
    assert [row["instruction"] for row in beta_tasks] == ["legacy beta"]

    alpha_subs = await list_subscriptions("1001")
    beta_subs = await list_subscriptions("2002")
    assert [row["title"] for row in alpha_subs] == ["Legacy Alpha Feed"]
    assert [row["title"] for row in beta_subs] == ["Legacy Beta Feed"]

    alpha_reminders = await get_pending_reminders("1001")
    beta_reminders = await get_pending_reminders("2002")
    assert [row["message"] for row in alpha_reminders] == ["legacy alpha reminder"]
    assert [row["message"] for row in beta_reminders] == ["legacy beta reminder"]

    alpha_watchlist = await get_user_watchlist("1001")
    beta_watchlist = await get_user_watchlist("2002")
    assert [row["stock_code"] for row in alpha_watchlist] == ["AAA"]
    assert [row["stock_code"] for row in beta_watchlist] == ["BBB"]


@pytest.mark.asyncio
async def test_heartbeat_store_keeps_legacy_shared_reads_user_scoped(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    heartbeat_store = HeartbeatStore()
    heartbeat_store.root = (tmp_path / "runtime_tasks").resolve()
    shared_dir = heartbeat_store.root / "user"
    shared_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_store._locks.clear()

    (shared_dir / "HEARTBEAT.md").write_text(
        """---
version: 2
user_id: "1001"
target: last
---

# Heartbeat checklist

- legacy alpha heartbeat
""",
        encoding="utf-8",
    )
    (shared_dir / "STATUS.json").write_text(
        json.dumps(
            {
                "version": 2,
                "user_id": "1001",
                "delivery": {"last_platform": "telegram", "last_chat_id": "chat-1"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    alpha_heartbeat = await heartbeat_store.get_state("1001")
    beta_heartbeat = await heartbeat_store.get_state("2002")

    assert alpha_heartbeat["checklist"] == ["legacy alpha heartbeat"]
    assert alpha_heartbeat["status"]["delivery"]["last_chat_id"] == "chat-1"
    assert beta_heartbeat["checklist"] == []
    assert beta_heartbeat["status"]["delivery"]["last_chat_id"] == ""


@pytest.mark.asyncio
async def test_state_store_keeps_legacy_reads_active_during_mixed_state(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await add_scheduled_task("0 8 * * *", "current alpha", user_id="1001")
    await create_subscription(
        "1001",
        {
            "title": "Current Alpha Feed",
            "feed_url": "https://example.com/current-alpha.xml",
        },
    )
    await add_reminder(
        "1001", "chat-1", "current alpha reminder", "2026-03-12T08:00:00+00:00"
    )
    await add_watchlist_stock("1001", "AAA", "Alpha")

    scheduled_path = shared_user_path("automation", "scheduled_tasks.md")
    scheduled_rows = await read_json(scheduled_path, [])
    scheduled_rows.append(
        {
            "id": 99,
            "user_id": "1001",
            "crontab": "0 10 * * *",
            "instruction": "legacy alpha",
            "platform": "telegram",
            "need_push": True,
            "is_active": True,
        }
    )
    await write_json(scheduled_path, scheduled_rows)

    subscriptions_path = shared_user_path("rss", "subscriptions.md")
    subscription_rows = await read_json(subscriptions_path, [])
    subscription_rows.append(
        {
            "id": 199,
            "user_id": "1001",
            "title": "Legacy Alpha Feed",
            "feed_url": "https://example.com/legacy-alpha.xml",
            "provider": "rss",
            "platform": "telegram",
        }
    )
    await write_json(subscriptions_path, subscription_rows)

    reminders_path = shared_user_path("automation", "reminders.md")
    reminder_rows = await read_json(reminders_path, [])
    reminder_rows.append(
        {
            "id": 299,
            "user_id": "1001",
            "chat_id": "chat-1",
            "message": "legacy alpha reminder",
            "trigger_time": "2026-03-12T09:00:00+00:00",
            "platform": "telegram",
        }
    )
    await write_json(reminders_path, reminder_rows)

    watchlist_path = shared_user_path("stock", "watchlist.md")
    watchlist_rows = await read_json(watchlist_path, [])
    watchlist_rows.append(
        {"user_id": "1001", "stock_code": "BBB", "stock_name": "Beta"}
    )
    await write_json(watchlist_path, watchlist_rows)

    assert [row["instruction"] for row in await get_all_active_tasks("1001")] == [
        "current alpha",
        "legacy alpha",
    ]
    assert [row["title"] for row in await list_subscriptions("1001")] == [
        "Current Alpha Feed",
        "Legacy Alpha Feed",
    ]
    assert [row["message"] for row in await get_pending_reminders("1001")] == [
        "current alpha reminder",
        "legacy alpha reminder",
    ]
    assert [row["stock_code"] for row in await get_user_watchlist("1001")] == [
        "AAA",
        "BBB",
    ]


@pytest.mark.asyncio
async def test_delete_operations_do_not_resurface_legacy_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await write_json(
        shared_user_path("automation", "scheduled_tasks.md"),
        [
            {
                "id": 11,
                "user_id": "1001",
                "crontab": "0 8 * * *",
                "instruction": "legacy alpha",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            }
        ],
    )
    await write_json(
        shared_user_path("rss", "subscriptions.md"),
        [
            {
                "id": 22,
                "user_id": "1001",
                "title": "Legacy Alpha Feed",
                "feed_url": "https://example.com/legacy-alpha.xml",
                "provider": "rss",
                "platform": "telegram",
            }
        ],
    )
    await write_json(
        shared_user_path("automation", "reminders.md"),
        [
            {
                "id": 33,
                "user_id": "1001",
                "chat_id": "chat-1",
                "message": "legacy alpha reminder",
                "trigger_time": "2026-03-12T08:00:00+00:00",
                "platform": "telegram",
            }
        ],
    )
    await write_json(
        shared_user_path("stock", "watchlist.md"),
        [{"user_id": "1001", "stock_code": "AAA", "stock_name": "Alpha"}],
    )

    await delete_task(11, user_id="1001")
    assert await get_all_active_tasks("1001") == []

    await delete_subscription("1001", 22)
    assert await list_subscriptions("1001") == []

    await delete_reminder(33, user_id="1001")
    assert await get_pending_reminders("1001") == []

    assert await remove_watchlist_stock("1001", "AAA") is True
    assert await get_user_watchlist("1001") == []


@pytest.mark.asyncio
async def test_new_ids_advance_past_legacy_shared_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await write_json(
        shared_user_path("rss", "subscriptions.md"),
        [
            {
                "id": 41,
                "user_id": "1001",
                "title": "Legacy Alpha Feed",
                "feed_url": "https://example.com/legacy-alpha.xml",
                "provider": "rss",
                "platform": "telegram",
            }
        ],
    )
    await write_json(
        shared_user_path("automation", "reminders.md"),
        [
            {
                "id": 52,
                "user_id": "1001",
                "chat_id": "chat-1",
                "message": "legacy alpha reminder",
                "trigger_time": "2026-03-12T08:00:00+00:00",
                "platform": "telegram",
            }
        ],
    )
    await write_json(
        shared_user_path("automation", "scheduled_tasks.md"),
        [
            {
                "id": 63,
                "user_id": "1001",
                "crontab": "0 8 * * *",
                "instruction": "legacy alpha",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            }
        ],
    )

    created_subscription = await create_subscription(
        "2002",
        {"title": "New Feed", "feed_url": "https://example.com/new.xml"},
    )
    reminder_id = await add_reminder(
        "2002", "chat-2", "new reminder", "2026-03-12T09:00:00+00:00"
    )
    task_id = await add_scheduled_task("0 9 * * *", "new task", user_id="2002")

    assert created_subscription["id"] > 41
    assert reminder_id > 52
    assert task_id > 63


@pytest.mark.asyncio
async def test_state_store_preserves_explicit_zero_user_id(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await create_subscription(
        0,
        {"title": "Zero Feed", "feed_url": "https://example.com/zero.xml"},
    )
    await add_reminder(0, "chat-0", "zero reminder", "2026-03-12T08:00:00+00:00")
    await add_scheduled_task("0 8 * * *", "zero task", user_id=0)

    subscriptions = await list_subscriptions(0)
    reminders = await get_pending_reminders(0)
    tasks = await get_all_active_tasks(0)

    assert subscriptions[0]["user_id"] == "0"
    assert reminders[0]["user_id"] == "0"
    assert tasks[0]["user_id"] == "0"


@pytest.mark.asyncio
async def test_all_user_ids_keeps_logical_user_named_user(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await create_subscription(
        "user",
        {"title": "Reserved Feed", "feed_url": "https://example.com/user.xml"},
    )

    assert "user" in all_user_ids()
    assert [row["title"] for row in await list_all_subscriptions()] == ["Reserved Feed"]


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="single-user canonical root no longer uses legacy migration flow"
)
async def test_migrate_legacy_user_state_copies_supported_domains_and_persists_report(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await write_json(
        shared_user_path("automation", "scheduled_tasks.md"),
        [
            {
                "id": 11,
                "user_id": "1001",
                "crontab": "0 8 * * *",
                "instruction": "legacy alpha",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            }
        ],
    )
    await write_json(
        shared_user_path("rss", "subscriptions.md"),
        [
            {
                "id": 22,
                "user_id": "1001",
                "title": "Legacy Alpha Feed",
                "feed_url": "https://example.com/legacy-alpha.xml",
                "provider": "rss",
                "platform": "telegram",
            }
        ],
    )
    await write_json(
        shared_user_path("automation", "reminders.md"),
        [
            {
                "id": 33,
                "user_id": "1001",
                "chat_id": "chat-1",
                "message": "legacy alpha reminder",
                "trigger_time": "2026-03-12T08:00:00+00:00",
                "platform": "telegram",
            }
        ],
    )
    await write_json(
        shared_user_path("stock", "watchlist.md"),
        [{"user_id": "1001", "stock_code": "AAA", "stock_name": "Alpha"}],
    )

    heartbeat_store = HeartbeatStore()
    heartbeat_store.root = (tmp_path / "runtime_tasks").resolve()
    shared_dir = heartbeat_store.root / "user"
    shared_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_store._locks.clear()
    (shared_dir / "HEARTBEAT.md").write_text(
        """---
version: 2
user_id: "1001"
target: last
---

# Heartbeat checklist

- legacy alpha heartbeat
""",
        encoding="utf-8",
    )
    (shared_dir / "STATUS.json").write_text(
        json.dumps(
            {
                "version": 2,
                "user_id": "1001",
                "delivery": {"last_platform": "telegram", "last_chat_id": "chat-1"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report = await migrate_legacy_user_state()

    assert report["summary"] == {
        "migrated": 5,
        "skipped": 0,
        "ambiguous": 0,
        "domains": ["heartbeat", "reminders", "rss", "scheduler", "watchlist"],
    }
    assert report["domains"]["scheduler"]["migrated"] == 1
    assert report["domains"]["rss"]["migrated"] == 1
    assert report["domains"]["reminders"]["migrated"] == 1
    assert report["domains"]["watchlist"]["migrated"] == 1
    assert report["domains"]["heartbeat"]["migrated"] == 1

    assert [row["instruction"] for row in await get_all_active_tasks("1001")] == [
        "legacy alpha"
    ]
    assert [row["title"] for row in await list_subscriptions("1001")] == [
        "Legacy Alpha Feed"
    ]
    assert [row["message"] for row in await get_pending_reminders("1001")] == [
        "legacy alpha reminder"
    ]
    assert [row["stock_code"] for row in await get_user_watchlist("1001")] == ["AAA"]
    assert (await heartbeat_store.get_state("1001"))["checklist"] == [
        "legacy alpha heartbeat"
    ]

    persisted_report = await read_json(system_path(*MIGRATION_REPORT_PATH_PARTS), {})
    assert persisted_report["summary"] == report["summary"]
    assert persisted_report["domains"]["heartbeat"]["migrated"] == 1
    assert persisted_report["history"][-1]["domains"]["heartbeat"]["migrated"] == 1


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="single-user canonical root no longer uses legacy migration flow"
)
async def test_migrate_legacy_user_state_is_idempotent_and_reports_ambiguous_rows(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await write_json(
        shared_user_path("automation", "scheduled_tasks.md"),
        [
            {
                "id": 11,
                "user_id": "1001",
                "crontab": "0 8 * * *",
                "instruction": "legacy alpha",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            },
            {
                "id": 12,
                "crontab": "0 9 * * *",
                "instruction": "missing owner",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            },
        ],
    )

    heartbeat_store = HeartbeatStore()
    heartbeat_store.root = (tmp_path / "runtime_tasks").resolve()
    shared_dir = heartbeat_store.root / "user"
    shared_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_store._locks.clear()
    (shared_dir / "HEARTBEAT.md").write_text(
        """---
version: 2
target: last
---

# Heartbeat checklist

- ambiguous heartbeat
""",
        encoding="utf-8",
    )

    first_report = await migrate_legacy_user_state()
    second_report = await migrate_legacy_user_state()

    assert first_report["domains"]["scheduler"] == {
        "migrated": 1,
        "skipped": 0,
        "ambiguous": 1,
    }
    assert first_report["domains"]["heartbeat"] == {
        "migrated": 0,
        "skipped": 0,
        "ambiguous": 1,
    }
    assert second_report["domains"]["scheduler"] == {
        "migrated": 0,
        "skipped": 1,
        "ambiguous": 1,
    }
    assert second_report["domains"]["heartbeat"] == {
        "migrated": 0,
        "skipped": 0,
        "ambiguous": 1,
    }
    assert second_report["summary"] == {
        "migrated": 0,
        "skipped": 1,
        "ambiguous": 2,
        "domains": ["heartbeat", "reminders", "rss", "scheduler", "watchlist"],
    }

    persisted_report = await read_json(system_path(*MIGRATION_REPORT_PATH_PARTS), {})
    assert len(persisted_report["history"]) == 2
    assert persisted_report["history"][0]["domains"]["scheduler"]["migrated"] == 1
    assert persisted_report["history"][1]["domains"]["scheduler"]["skipped"] == 1


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="single-user canonical root no longer uses legacy migration flow"
)
async def test_migrate_legacy_user_state_handles_partial_heartbeat_and_bad_rows(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await write_json(
        shared_user_path("automation", "scheduled_tasks.md"),
        [
            {
                "id": 11,
                "user_id": "1001",
                "crontab": "0 8 * * *",
                "instruction": "legacy alpha",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            },
            {
                "id": "bad-id",
                "user_id": "1001",
                "crontab": "0 9 * * *",
                "instruction": "broken row",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            },
            {
                "user_id": "1001",
                "crontab": "0 10 * * *",
                "instruction": "missing id",
                "platform": "telegram",
                "need_push": True,
                "is_active": True,
            },
        ],
    )

    heartbeat_store = HeartbeatStore()
    heartbeat_store.root = (tmp_path / "runtime_tasks").resolve()
    shared_dir = heartbeat_store.root / "user"
    shared_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_store._locks.clear()
    (shared_dir / "HEARTBEAT.md").write_text(
        """---
version: 2
user_id: "1001"
target: last
---

# Heartbeat checklist

- legacy alpha heartbeat
""",
        encoding="utf-8",
    )
    (shared_dir / "STATUS.json").write_text(
        json.dumps(
            {
                "version": 2,
                "user_id": "1001",
                "delivery": {"last_platform": "telegram", "last_chat_id": "chat-1"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    user_dir = heartbeat_store.root / "1001"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "HEARTBEAT.md").write_text(
        """---
version: 2
user_id: "1001"
target: last
---

# Heartbeat checklist

- existing canonical heartbeat
""",
        encoding="utf-8",
    )

    report = await migrate_legacy_user_state()

    assert report["domains"]["scheduler"] == {
        "migrated": 1,
        "skipped": 0,
        "ambiguous": 2,
    }
    assert report["domains"]["heartbeat"] == {
        "migrated": 1,
        "skipped": 0,
        "ambiguous": 0,
    }
    assert report["domains"]["watchlist"] == {
        "migrated": 0,
        "skipped": 0,
        "ambiguous": 0,
    }
    assert (await heartbeat_store.get_state("1001"))["checklist"] == [
        "existing canonical heartbeat"
    ]
    assert (await heartbeat_store.get_state("1001"))["status"]["delivery"] == {
        "last_platform": "telegram",
        "last_chat_id": "chat-1",
    }


@pytest.mark.asyncio
async def test_migrate_legacy_user_state_counts_bad_watchlist_rows_as_ambiguous(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await write_json(
        shared_user_path("stock", "watchlist.md"),
        [
            {"user_id": "1001", "stock_code": "", "stock_name": "Broken"},
            {"user_id": "1001", "stock_name": "Missing Code"},
        ],
    )

    first_report = await migrate_legacy_user_state()
    second_report = await migrate_legacy_user_state()

    assert first_report["domains"]["watchlist"] == {
        "migrated": 0,
        "skipped": 0,
        "ambiguous": 2,
    }
    assert second_report["domains"]["watchlist"] == {
        "migrated": 0,
        "skipped": 0,
        "ambiguous": 2,
    }
