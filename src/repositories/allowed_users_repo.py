"""
白名单用户 Repository
"""

import aiosqlite
from .base import get_db


async def add_allowed_user(
    user_id: int | str, added_by: int | str = None, description: str = None
):
    """添加用户到白名单"""
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO allowed_users (user_id, added_by, description) VALUES (?, ?, ?)",
            (user_id, added_by, description),
        )
        await db.commit()


async def remove_allowed_user(user_id: int | str):
    """从白名单移除用户"""
    async with await get_db() as db:
        await db.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_allowed_users() -> list[dict]:
    """获取所有白名单用户"""
    async with await get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM allowed_users") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def check_user_allowed_in_db(user_id: int | str) -> bool:
    """检查用户是否在 DB 白名单中"""
    async with await get_db() as db:
        async with db.execute(
            "SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None
