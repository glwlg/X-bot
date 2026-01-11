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
                message_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 迁移：chat_history 添加 message_id
        try:
            async with db.execute("PRAGMA table_info(chat_history)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]
            if "message_id" not in columns:
                await db.execute("ALTER TABLE chat_history ADD COLUMN message_id INTEGER")
                logger.info("Added column message_id to chat_history")
        except Exception as e:
            logger.error(f"Migration error (chat_history): {e}")
        
        # 用户统计表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                ai_chats INTEGER DEFAULT 0,
                downloads INTEGER DEFAULT 0,
                image_generations INTEGER DEFAULT 0,
                photo_analyses INTEGER DEFAULT 0,
                video_analyses INTEGER DEFAULT 0,
                voice_chats INTEGER DEFAULT 0,
                doc_analyses INTEGER DEFAULT 0,
                video_summaries INTEGER DEFAULT 0,
                translations_count INTEGER DEFAULT 0,
                reminders_set INTEGER DEFAULT 0,
                subscriptions_added INTEGER DEFAULT 0,
                first_use TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_use TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 简单迁移逻辑：检查并添加新字段 (针对旧数据库)
        try:
            async with db.execute("PRAGMA table_info(user_stats)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]
                
            new_columns = [
                ("voice_chats", "INTEGER DEFAULT 0"),
                ("doc_analyses", "INTEGER DEFAULT 0"),
                ("video_summaries", "INTEGER DEFAULT 0"),
                ("translations_count", "INTEGER DEFAULT 0"),
                ("reminders_set", "INTEGER DEFAULT 0"),
                ("subscriptions_added", "INTEGER DEFAULT 0"),
            ]
            
            for col_name, col_def in new_columns:
                if col_name not in columns:
                    await db.execute(f"ALTER TABLE user_stats ADD COLUMN {col_name} {col_def}")
                    logger.info(f"Added column {col_name} to user_stats")
                    
        except Exception as e:
            logger.error(f"Migration error: {e}")
        
        # 提醒任务表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                trigger_time TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 用户设置表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                auto_translate BOOLEAN DEFAULT 0,
                target_lang TEXT DEFAULT 'zh-CN',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        
        # 订阅表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                feed_url TEXT NOT NULL,
                title TEXT,
                last_etag TEXT,
                last_modified TEXT,
                last_entry_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, feed_url)
            )
        """)

        # 允许用户表 (白名单)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS allowed_users (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

async def add_chat_message(user_id: int, role: str, content: str, message_id: int = None):
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO chat_history (user_id, role, content, message_id) VALUES (?, ?, ?, ?)",
            (user_id, role, content, message_id)
        )
        await db.commit()
        
        # 仅保留最近 20 条（或者按需保留）
        # 这里可以选择不删除，或者定期清理。简单起见，这里不每次都清理。


async def get_chat_message(message_id: int) -> str | None:
    """根据 message_id 获取消息内容"""
    if not message_id:
        return None
    async with await get_db() as db:
        async with db.execute(
            "SELECT content FROM chat_history WHERE message_id = ?", 
            (message_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


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


async def clear_chat_history(user_id: int):
    """清除用户的聊天历史"""
    async with await get_db() as db:
        await db.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        await db.commit()


# --- 用户统计操作 ---

async def increment_stat(user_id: int, stat_name: str, count: int = 1):
    valid_stats = {
        "ai_chats", "downloads", "image_generations", "photo_analyses", 
        "video_analyses", "voice_chats", "doc_analyses", "video_summaries",
        "translations_count", "reminders_set", "subscriptions_added"
    }
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


# --- 提醒事项操作 ---

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
        # 按触发时间排序
        async with db.execute(
            "SELECT * FROM reminders ORDER BY trigger_time ASC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


# --- 用户设置操作 ---

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
            if row:
                return dict(row)
            # 默认设置
            return {"user_id": user_id, "auto_translate": 0, "target_lang": "zh-CN"}


# --- 订阅操作 ---

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


# --- 白名单用户操作 ---

async def add_allowed_user(user_id: int, added_by: int = None, description: str = None):
    """添加用户到白名单"""
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO allowed_users (user_id, added_by, description) VALUES (?, ?, ?)",
            (user_id, added_by, description)
        )
        await db.commit()


async def remove_allowed_user(user_id: int):
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


async def check_user_allowed_in_db(user_id: int) -> bool:
    """检查用户是否在 DB 白名单中"""
    async with await get_db() as db:
        async with db.execute(
            "SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None
