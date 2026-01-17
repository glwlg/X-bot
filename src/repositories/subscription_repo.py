"""
订阅 Repository
"""
import aiosqlite
from .base import get_db


async def add_subscription(user_id: int, feed_url: str, title: str):
    """添加订阅"""
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO subscriptions (user_id, feed_url, title) VALUES (?, ?, ?)",
            (user_id, feed_url, title)
        )
        await db.commit()


async def delete_subscription(user_id: int, feed_url: str):
    """删除订阅"""
    async with await get_db() as db:
        await db.execute(
            "DELETE FROM subscriptions WHERE user_id = ? AND feed_url = ?",
            (user_id, feed_url)
        )
        await db.commit()


async def delete_subscription_by_id(sub_id: int, user_id: int) -> bool:
    """根据订阅 ID 删除订阅（需验证用户归属）"""
    async with await get_db() as db:
        cursor = await db.execute(
            "DELETE FROM subscriptions WHERE id = ? AND user_id = ?",
            (sub_id, user_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_user_subscriptions(user_id: int) -> list[dict]:
    """获取用户的订阅列表"""
    async with await get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_all_subscriptions() -> list[dict]:
    """获取所有订阅（用于后台刷新）"""
    async with await get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM subscriptions") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def update_subscription_status(sub_id: int, last_entry_hash: str, last_etag: str = None, last_modified: str = None):
    """更新订阅状态（更新时间和哈希）"""
    async with await get_db() as db:
        await db.execute(
            """
            UPDATE subscriptions 
            SET last_entry_hash = ?, last_etag = ?, last_modified = ? 
            WHERE id = ?
            """,
            (last_entry_hash, last_etag, last_modified, sub_id)
        )
        await db.commit()
