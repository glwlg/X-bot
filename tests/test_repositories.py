"""
Repository 模块单元测试
"""

import pytest
import asyncio


class TestRepositoryBase:
    """测试数据库基础功能"""

    @pytest.mark.asyncio
    async def test_init_db(self, mock_db):
        """测试数据库初始化"""
        from repositories.base import init_db, DB_PATH
        import os

        await init_db()

        # 验证数据库文件已创建
        assert os.path.exists(DB_PATH)

    @pytest.mark.asyncio
    async def test_get_db_connection(self, mock_db):
        """测试数据库连接获取"""
        from repositories.base import init_db, get_db

        await init_db()

        async with await get_db() as db:
            # 验证连接可用
            async with db.execute("SELECT 1") as cursor:
                row = await cursor.fetchone()
                assert row[0] == 1


class TestWatchlistRepo:
    """测试自选股 Repository"""

    @pytest.mark.asyncio
    async def test_add_watchlist_stock(self, mock_db):
        """测试添加自选股"""
        from repositories.base import init_db
        from repositories.watchlist_repo import add_watchlist_stock, get_user_watchlist

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
        from repositories.base import init_db
        from repositories.watchlist_repo import add_watchlist_stock

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
        from repositories.base import init_db
        from repositories.watchlist_repo import (
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
        from repositories.base import init_db
        from repositories.reminder_repo import add_reminder, get_pending_reminders

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
        from repositories.base import init_db
        from repositories.reminder_repo import (
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
        from repositories.base import init_db
        from repositories.subscription_repo import (
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
        from repositories.base import init_db
        from repositories.subscription_repo import (
            add_subscription,
            delete_subscription,
            get_user_subscriptions,
        )

        await init_db()

        await add_subscription(12345, "https://example.com/rss", "测试订阅")
        await delete_subscription(12345, "https://example.com/rss")

        subs = await get_user_subscriptions(12345)
        assert len(subs) == 0
