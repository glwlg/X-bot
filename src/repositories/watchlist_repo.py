"""
自选股 Repository
"""

import aiosqlite
from .base import get_db


async def add_watchlist_stock(
    user_id: int, stock_code: str, stock_name: str, platform: str = "telegram"
) -> bool:
    """添加自选股"""
    async with await get_db() as db:
        try:
            await db.execute(
                "INSERT INTO watchlist (user_id, stock_code, stock_name, platform) VALUES (?, ?, ?, ?)",
                (user_id, stock_code, stock_name, platform),
            )
            await db.commit()
            return True
        except Exception:
            return False


async def remove_watchlist_stock(stock_id: int) -> bool:
    """删除自选股"""
    async with await get_db() as db:
        cursor = await db.execute(
            "DELETE FROM watchlist WHERE id = ?",
            (stock_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_user_watchlist(user_id: int, platform: str = None) -> list[dict]:
    """获取用户自选股列表"""
    async with await get_db() as db:
        db.row_factory = aiosqlite.Row

        sql = "SELECT * FROM watchlist WHERE user_id = ?"
        params = [user_id]

        if platform:
            sql += " AND platform = ?"
            params.append(platform)

        sql += " ORDER BY created_at DESC"

        async with db.execute(sql, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_all_watchlist_users() -> list[tuple[int, str]]:
    """获取所有有自选股的用户 ID 和平台"""
    async with await get_db() as db:
        async with db.execute(
            "SELECT DISTINCT user_id, platform FROM watchlist"
        ) as cursor:
            rows = await cursor.fetchall()
            # Handle legacy rows where platform might be null (though we set default)
            # Ensure we return tuple (user_id, platform)
            results = []
            for row in rows:
                uid = row[0]
                plat = row[1] if len(row) > 1 and row[1] else "telegram"
                results.append((uid, plat))
            return results
