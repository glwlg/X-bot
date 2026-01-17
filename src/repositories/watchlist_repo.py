"""
自选股 Repository
"""
import aiosqlite
from .base import get_db


async def add_watchlist_stock(user_id: int, stock_code: str, stock_name: str) -> bool:
    """添加自选股"""
    async with await get_db() as db:
        try:
            await db.execute(
                "INSERT INTO watchlist (user_id, stock_code, stock_name) VALUES (?, ?, ?)",
                (user_id, stock_code, stock_name)
            )
            await db.commit()
            return True
        except Exception:
            return False


async def remove_watchlist_stock(user_id: int, stock_code: str) -> bool:
    """删除自选股"""
    async with await get_db() as db:
        cursor = await db.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND stock_code = ?",
            (user_id, stock_code)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_user_watchlist(user_id: int) -> list[dict]:
    """获取用户自选股列表"""
    async with await get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM watchlist WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_all_watchlist_users() -> list[int]:
    """获取所有有自选股的用户 ID"""
    async with await get_db() as db:
        async with db.execute(
            "SELECT DISTINCT user_id FROM watchlist"
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
