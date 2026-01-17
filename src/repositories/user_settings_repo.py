"""
用户设置 Repository
"""
import aiosqlite
from .base import get_db


async def set_translation_mode(user_id: int, enabled: bool):
    """设置自动翻译模式开关"""
    async with await get_db() as db:
        await db.execute(
            """
            INSERT INTO user_settings (user_id, auto_translate) 
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET 
            auto_translate = ?, updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, enabled, enabled)
        )
        await db.commit()


async def get_user_settings(user_id: int) -> dict:
    """获取用户设置"""
    async with await get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_settings WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            # 默认设置
            return {"user_id": user_id, "auto_translate": 0, "target_lang": "zh-CN"}
