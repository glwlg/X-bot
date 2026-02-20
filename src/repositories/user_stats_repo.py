"""
用户统计 Repository
"""

import logging
import aiosqlite
from .base import get_db

logger = logging.getLogger(__name__)


async def increment_stat(user_id: int | str, stat_name: str, count: int = 1):
    valid_stats = {
        "ai_chats",
        "downloads",
        "image_generations",
        "photo_analyses",
        "video_analyses",
        "voice_chats",
        "doc_analyses",
        "video_summaries",
        "translations_count",
        "reminders_set",
        "subscriptions_added",
    }
    if stat_name not in valid_stats:
        logger.error(f"Invalid stat name: {stat_name}")
        return

    async with await get_db() as db:
        # 确保用户存在
        await db.execute(
            "INSERT OR IGNORE INTO user_stats (user_id) VALUES (?)", (user_id,)
        )
        # 更新统计和最后使用时间
        await db.execute(
            f"""
            UPDATE user_stats 
            SET {stat_name} = {stat_name} + ?, last_use = CURRENT_TIMESTAMP 
            WHERE user_id = ?
            """,
            (count, user_id),
        )
        await db.commit()


async def get_user_stats(user_id: int | str) -> dict | None:
    async with await get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_stats WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None
