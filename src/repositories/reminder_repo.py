"""
提醒任务 Repository
"""
import aiosqlite
from .base import get_db


async def add_reminder(user_id: int, chat_id: int, message: str, trigger_time: str) -> int:
    """添加提醒任务"""
    async with await get_db() as db:
        cursor = await db.execute(
            "INSERT INTO reminders (user_id, chat_id, message, trigger_time) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, message, trigger_time)
        )
        await db.commit()
        return cursor.lastrowid


async def delete_reminder(reminder_id: int):
    """删除提醒任务"""
    async with await get_db() as db:
        await db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await db.commit()


async def get_pending_reminders() -> list[dict]:
    """获取所有未执行的提醒任务"""
    async with await get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM reminders ORDER BY trigger_time ASC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
