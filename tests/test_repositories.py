"""
Repository 模块单元测试
"""

# pyright: reportMissingImports=false,reportUnknownParameterType=false,reportMissingParameterType=false,reportUnknownVariableType=false,reportUnknownMemberType=false,reportUnknownArgumentType=false

import pytest
import re


class TestRepositoryBase:
    """测试 Repository 基础能力"""

    @pytest.mark.asyncio
    async def test_init_db(self, mock_db):
        """测试仓储目录初始化"""
        from core.state_io import init_db
        from core.state_paths import repo_root, users_root

        await init_db()

        assert repo_root().exists()
        assert users_root().exists()

    @pytest.mark.asyncio
    async def test_next_id_counter(self, mock_db):
        """测试 ID 计数器递增"""
        from core.state_io import init_db, next_id, read_json
        from core.state_paths import system_path

        await init_db()

        first = await next_id("test_counter", start=10)
        second = await next_id("test_counter", start=10)

        assert first == 10
        assert second == 11

        counters = await read_json(system_path("id_counters.md"), {})
        assert counters.get("test_counter") == 12


class TestWatchlistRepo:
    """测试自选股 Repository"""

    @pytest.mark.asyncio
    async def test_add_watchlist_stock(self, mock_db):
        """测试添加自选股"""
        from core.state_io import init_db
        from core.state_store import add_watchlist_stock, get_user_watchlist

        await init_db()

        # 添加股票
        success = await add_watchlist_stock(12345, "sh601006", "大秦铁路")
        assert success is True

        # 验证已添加
        watchlist = await get_user_watchlist(12345)
        assert len(watchlist) == 1
        assert watchlist[0]["stock_code"] == "sh601006"
        assert watchlist[0]["stock_name"] == "大秦铁路"

    @pytest.mark.asyncio
    async def test_add_duplicate_stock(self, mock_db):
        """测试重复添加自选股"""
        from core.state_io import init_db
        from core.state_store import add_watchlist_stock

        await init_db()

        # 第一次添加
        success1 = await add_watchlist_stock(12345, "sh601006", "大秦铁路")
        assert success1 is True

        # 重复添加应返回 False
        success2 = await add_watchlist_stock(12345, "sh601006", "大秦铁路")
        assert success2 is False

    @pytest.mark.asyncio
    async def test_remove_watchlist_stock(self, mock_db):
        """测试删除自选股"""
        from core.state_io import init_db
        from core.state_store import (
            add_watchlist_stock,
            remove_watchlist_stock,
            get_user_watchlist,
        )

        await init_db()

        # 添加后删除
        await add_watchlist_stock(12345, "sh601006", "大秦铁路")
        success = await remove_watchlist_stock(12345, "sh601006")
        assert success is True

        # 验证已删除
        watchlist = await get_user_watchlist(12345)
        assert len(watchlist) == 0


class TestReminderRepo:
    """测试提醒 Repository"""

    @pytest.mark.asyncio
    async def test_add_and_get_reminder(self, mock_db):
        """测试添加和获取提醒"""
        from core.state_io import init_db
        from core.state_store import add_reminder, get_pending_reminders

        await init_db()

        # 添加提醒
        reminder_id = await add_reminder(
            user_id=12345,
            chat_id=12345,
            message="测试提醒",
            trigger_time="2026-01-17T12:00:00+08:00",
        )

        assert reminder_id > 0

        # 获取待执行提醒
        reminders = await get_pending_reminders()
        assert len(reminders) == 1
        assert reminders[0]["message"] == "测试提醒"

    @pytest.mark.asyncio
    async def test_delete_reminder(self, mock_db):
        """测试删除提醒"""
        from core.state_io import init_db
        from core.state_store import (
            add_reminder,
            delete_reminder,
            get_pending_reminders,
        )

        await init_db()

        # 添加后删除
        reminder_id = await add_reminder(12345, 12345, "测试", "2026-01-17T12:00:00")
        await delete_reminder(reminder_id)

        # 验证已删除
        reminders = await get_pending_reminders()
        assert len(reminders) == 0


class TestSubscriptionRepo:
    """测试订阅 Repository"""

    @pytest.mark.asyncio
    async def test_add_subscription(self, mock_db):
        """测试添加订阅"""
        from core.state_io import init_db
        from core.state_store import (
            add_subscription,
            get_user_subscriptions,
        )

        await init_db()

        await add_subscription(12345, "https://example.com/rss", "测试订阅")

        subs = await get_user_subscriptions(12345)
        assert len(subs) == 1
        assert subs[0]["title"] == "测试订阅"

    @pytest.mark.asyncio
    async def test_delete_subscription(self, mock_db):
        """测试删除订阅"""
        from core.state_io import init_db
        from core.state_store import (
            add_subscription,
            delete_subscription,
            get_user_subscriptions,
        )

        await init_db()

        await add_subscription(12345, "https://example.com/rss", "测试订阅")
        await delete_subscription(12345, "https://example.com/rss")

        subs = await get_user_subscriptions(12345)
        assert len(subs) == 0


class TestAllowedUsersStateStore:
    @pytest.mark.asyncio
    async def test_add_check_remove_allowed_user(self, mock_db):
        from core.state_io import init_db
        from core.state_store import (
            add_allowed_user,
            check_user_allowed_in_db,
            get_allowed_users,
            remove_allowed_user,
        )

        await init_db()

        await add_allowed_user(1001, added_by=42, description="admin")
        await add_allowed_user(1001, added_by=99, description="duplicate")

        assert await check_user_allowed_in_db("1001") is True

        rows = await get_allowed_users()
        assert len(rows) == 1
        assert rows[0]["user_id"] == "1001"
        assert rows[0]["added_by"] == "42"
        assert rows[0]["description"] == "admin"
        assert rows[0]["created_at"]

        await remove_allowed_user("1001")

        assert await check_user_allowed_in_db(1001) is False
        assert await get_allowed_users() == []

    @pytest.mark.asyncio
    async def test_allowed_users_legacy_read_and_canonical_write(self, mock_db):
        from core.state_file import STATE_BEGIN_MARKER, STATE_END_MARKER
        from core.state_io import init_db
        from core.state_paths import system_path
        from core.state_store import add_allowed_user, get_allowed_users

        await init_db()

        path = system_path("allowed_users.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n- user_id: '2001'\n  description: legacy\n---\n",
            encoding="utf-8",
        )

        rows = await get_allowed_users()
        assert len(rows) == 1
        assert rows[0]["user_id"] == "2001"
        assert rows[0]["description"] == "legacy"

        await add_allowed_user(2002, added_by=1, description="new")

        content = path.read_text(encoding="utf-8")
        assert STATE_BEGIN_MARKER in content
        assert STATE_END_MARKER in content
        assert content.count("```yaml") == 1


class TestStateFileProtocol:
    """测试状态文件协议与兼容解析"""

    def test_render_state_markdown_has_canonical_markers_and_single_yaml_fence(self):
        """写入格式必须包含标准标记和单个 YAML fenced block"""
        from core.state_file import (
            STATE_BEGIN_MARKER,
            STATE_END_MARKER,
            parse_state_payload,
            render_state_markdown,
        )

        content = render_state_markdown({"name": "xbot"}, title="Protocol")

        assert content.count(STATE_BEGIN_MARKER) == 1
        assert content.count(STATE_END_MARKER) == 1
        assert content.count("```yaml") == 1
        assert len(re.findall(r"```yaml\n[\s\S]*?\n```", content)) == 1

        ok, payload = parse_state_payload(content)
        assert ok is True
        assert payload == {"version": 1, "name": "xbot"}

    @pytest.mark.parametrize(
        "raw_text,expected",
        [
            (
                "---\nfoo: bar\ncount: 2\n---\nlegacy frontmatter",
                {"foo": "bar", "count": 2},
            ),
            (
                "legacy\n```yaml\nfoo: fenced\ncount: 3\n```\ntext",
                {"foo": "fenced", "count": 3},
            ),
            (
                "foo: raw\ncount: 4\n",
                {"foo": "raw", "count": 4},
            ),
        ],
    )
    def test_parse_state_payload_supports_legacy_shapes(self, raw_text, expected):
        """旧格式前言块/无标记 fenced yaml/raw yaml 均可解析"""
        from core.state_file import parse_state_payload

        ok, payload = parse_state_payload(raw_text)

        assert ok is True
        assert payload == expected


class TestRepositoryStateFiles:
    """测试仓储状态文件读写与保护行为"""

    @pytest.mark.asyncio
    async def test_write_json_creates_backup_when_existing_file_unparsable(
        self, mock_db
    ):
        """已有内容不可解析时，写入前创建带时间戳备份"""
        from core.state_io import read_json, write_json
        from core.state_paths import user_path

        target = user_path("42", "settings.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(":::not-yaml:::\n---broken", encoding="utf-8")

        await write_json(target, {"foo": "bar"})

        backups = sorted(target.parent.glob("settings.md.bak-*"))
        assert len(backups) == 1
        assert re.match(r"settings\.md\.bak-\d{8}-\d{6}$", backups[0].name)
        assert backups[0].read_text(encoding="utf-8") == ":::not-yaml:::\n---broken"

        loaded = await read_json(target, {})
        assert loaded == {"version": 1, "foo": "bar"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "label,path_parts,payload,expected",
        [
            (
                "settings",
                ("user", "7", ("settings.md",)),
                {"auto_translate": 1, "target_lang": "zh-CN"},
                {"version": 1, "auto_translate": 1, "target_lang": "zh-CN"},
            ),
            (
                "subscriptions",
                ("user", "7", ("rss", "subscriptions.md")),
                [{"feed_url": "https://example.com/rss", "title": "Example"}],
                [{"feed_url": "https://example.com/rss", "title": "Example"}],
            ),
            (
                "watchlist",
                ("user", "7", ("stock", "watchlist.md")),
                [{"stock_code": "sh601006", "stock_name": "大秦铁路"}],
                [{"stock_code": "sh601006", "stock_name": "大秦铁路"}],
            ),
            (
                "reminders",
                ("user", "7", ("automation", "reminders.md")),
                [{"id": 1, "message": "drink water"}],
                [{"id": 1, "message": "drink water"}],
            ),
            (
                "scheduled_tasks",
                ("user", "7", ("automation", "scheduled_tasks.md")),
                [{"id": 2, "crontab": "*/5 * * * *", "instruction": "check"}],
                [{"id": 2, "crontab": "*/5 * * * *", "instruction": "check"}],
            ),
            (
                "allowed_users",
                ("system", None, ("allowed_users.md",)),
                [{"user_id": "1001", "description": "admin"}],
                [{"user_id": "1001", "description": "admin"}],
            ),
            (
                "cache",
                ("system", None, ("video_cache.md",)),
                {"abc": {"file_path": "/tmp/a.mp4"}},
                {"version": 1, "abc": {"file_path": "/tmp/a.mp4"}},
            ),
            (
                "counters",
                ("system", None, ("id_counters.md",)),
                {"reminder": 2, "scheduled_task": 8},
                {"version": 1, "reminder": 2, "scheduled_task": 8},
            ),
        ],
    )
    async def test_scoped_state_files_roundtrip_by_domain(
        self, mock_db, label, path_parts, payload, expected
    ):
        """各域状态文件按作用域写入并可回读"""
        from core.state_io import read_json, write_json
        from core.state_paths import system_path, user_path

        scope, uid, parts = path_parts
        path = user_path(uid, *parts) if scope == "user" else system_path(*parts)

        await write_json(path, payload)
        loaded = await read_json(path, None)

        assert loaded == expected, label
        assert path.exists(), label
