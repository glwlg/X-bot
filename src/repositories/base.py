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
        # Enable WAL mode for better concurrency
        await db.execute("PRAGMA journal_mode=WAL;")

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
                    await db.execute(
                        f"ALTER TABLE user_stats ADD COLUMN {col_name} {col_def}"
                    )
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

        # 对话历史表 (用于上下文持久化)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL, -- 'user' or 'model'
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT -- 用于区分不同会话（/new 可重置 session_id，但不物理删除）
            )
        """)

        # 定时任务表 (Scheduled Tasks)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_name TEXT NOT NULL,
                crontab TEXT NOT NULL,         -- e.g. '0 8 * * *'
                instruction TEXT NOT NULL,     -- The prompt/instruction to execute
                user_id INTEGER DEFAULT 0,
                platform TEXT DEFAULT 'telegram',
                need_push BOOLEAN DEFAULT 1,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 索引
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_active ON scheduled_tasks(is_active)"
        )

        # 迁移逻辑：确保 chat_history 有 session_id
        try:
            async with db.execute("PRAGMA table_info(chat_history)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]

            if "session_id" not in columns:
                await db.execute("ALTER TABLE chat_history ADD COLUMN session_id TEXT")
                logger.info("Added column session_id to chat_history")

        except Exception as e:
            logger.error(f"Migration error (chat_history): {e}")

        # 迁移逻辑：确保 subscriptions 和 reminders 有 platform 字段
        try:
            # Subscriptions
            async with db.execute("PRAGMA table_info(subscriptions)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]

            if "platform" not in columns:
                await db.execute(
                    "ALTER TABLE subscriptions ADD COLUMN platform TEXT DEFAULT 'telegram'"
                )
                logger.info("Added column platform to subscriptions")

            # Reminders
            async with db.execute("PRAGMA table_info(reminders)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]

            if "platform" not in columns:
                await db.execute(
                    "ALTER TABLE reminders ADD COLUMN platform TEXT DEFAULT 'telegram'"
                )
                logger.info("Added column platform to reminders")

            # Watchlist
            async with db.execute("PRAGMA table_info(watchlist)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]

            if "platform" not in columns:
                await db.execute(
                    "ALTER TABLE watchlist ADD COLUMN platform TEXT DEFAULT 'telegram'"
                )
                logger.info("Added column platform to watchlist")

            # Scheduled Tasks (Migration)
            async with db.execute("PRAGMA table_info(scheduled_tasks)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]

            new_task_columns = [
                ("user_id", "INTEGER DEFAULT 0"),
                ("platform", "TEXT DEFAULT 'telegram'"),
                ("need_push", "BOOLEAN DEFAULT 1"),  # Default to True for now
            ]

            for col_name, col_def in new_task_columns:
                if col_name not in columns:
                    await db.execute(
                        f"ALTER TABLE scheduled_tasks ADD COLUMN {col_name} {col_def}"
                    )
                    logger.info(f"Added column {col_name} to scheduled_tasks")

        except Exception as e:
            logger.error(f"Migration error (tables): {e}")

        # 索引
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_history_user_session ON chat_history(user_id, session_id)"
        )

        # 账号管理表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                enc_data TEXT NOT NULL, -- JSON string
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, service)
            )
        """)

        await db.commit()
    logger.info("Database initialized successfully")
