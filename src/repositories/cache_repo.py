"""
视频缓存 Repository
"""
import aiosqlite
from .base import get_db


async def save_video_cache(file_id: str, file_path: str):
    async with await get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO video_cache (file_id, file_path) VALUES (?, ?)",
            (file_id, file_path)
        )
        await db.commit()


async def get_video_cache(file_id: str) -> str | None:
    async with await get_db() as db:
        async with db.execute("SELECT file_path FROM video_cache WHERE file_id = ?", (file_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None
