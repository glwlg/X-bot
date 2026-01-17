"""
数据库基础模块 - 提供数据库连接和初始化
"""
import logging
import os
import aiosqlite

from core.config import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(DATA_DIR, "bot_data.db")


async def get_db():
    """获取数据库连接"""
    return aiosqlite.connect(DB_PATH)


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
        
        # 简单迁移逻辑：检查并添加新字段
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
        
        # 自选股表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, stock_code)
            )
        """)
        
        await db.commit()
    logger.info("Database initialized successfully")
