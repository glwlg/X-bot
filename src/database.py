"""
数据库管理模块 - 使用 SQLite 持久化数据
"""
import logging
import aiosqlite
import os

from config import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(DATA_DIR, "bot_data.db")


async def init_db():
    """初始化数据库"""
    logger.info(f"Initializing database at {DB_PATH}")
    async with aiosqlite.connect(DB_PATH) as db:
        # 视频缓存表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS video_cache (
                file_id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 聊天记录表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 用户统计表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                ai_chats INTEGER DEFAULT 0,
                downloads INTEGER DEFAULT 0,
                image_generations INTEGER DEFAULT 0,
                photo_analyses INTEGER DEFAULT 0,
                video_analyses INTEGER DEFAULT 0,
                first_use TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_use TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await db.commit()
    logger.info("Database initialized successfully")


async def get_db():
    """获取数据库连接（上下文管理器）"""
    return aiosqlite.connect(DB_PATH)


# --- 视频缓存操作 ---

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


# --- 聊天记录操作 ---

async def add_chat_message(user_id: int, role: str, content: str):
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content)
        )
        await db.commit()
        
        # 仅保留最近 20 条（或者按需保留）
        # 这里可以选择不删除，或者定期清理。简单起见，这里不每次都清理。


async def get_chat_history(user_id: int, limit: int = 10) -> list[dict]:
    async with await get_db() as db:
        async with db.execute(
            """
            SELECT role, content FROM chat_history 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT ?
            """,
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            # 数据库取出来是倒序，需要反转回正序
            history = [{"role": row[0], "parts": [{"text": row[1]}]} for row in reversed(rows)]
            return history


# --- 用户统计操作 ---

async def increment_stat(user_id: int, stat_name: str, count: int = 1):
    valid_stats = {"ai_chats", "downloads", "image_generations", "photo_analyses", "video_analyses"}
    if stat_name not in valid_stats:
        logger.error(f"Invalid stat name: {stat_name}")
        return

    async with await get_db() as db:
        # 确保用户存在
        await db.execute(
            "INSERT OR IGNORE INTO user_stats (user_id) VALUES (?)",
            (user_id,)
        )
        # 更新统计和最后使用时间
        await db.execute(
            f"""
            UPDATE user_stats 
            SET {stat_name} = {stat_name} + ?, last_use = CURRENT_TIMESTAMP 
            WHERE user_id = ?
            """,
            (count, user_id)
        )
        await db.commit()


async def get_user_stats(user_id: int) -> dict | None:
    async with await get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_stats WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None
