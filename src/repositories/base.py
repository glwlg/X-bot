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

        # --- Migration Helpers ---
        async def migrate_table_pk_to_text(
            table_name: str, create_sql: str, columns_to_copy: list[str]
        ):
            """
            Migrate a table to use TEXT PRIMARY KEY instead of INTEGER PRIMARY KEY.
            """
            try:
                # Check current type of user_id
                async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
                    # (cid, name, type, notnull, dflt_value, pk)
                    cols = await cursor.fetchall()

                needs_migration = False
                for col in cols:
                    if col[1] == "user_id" and "INT" in col[2].upper() and col[5] == 1:
                        needs_migration = True
                        break

                if not needs_migration:
                    # If table doesn't exist, create it with new schema
                    await db.execute(create_sql)
                    return

                logger.info(f"Migrating {table_name} to support TEXT user_id...")

                # 1. Rename old table
                temp_table = f"{table_name}_old_int_pk"
                await db.execute(f"DROP TABLE IF EXISTS {temp_table}")
                await db.execute(f"ALTER TABLE {table_name} RENAME TO {temp_table}")

                # 2. Create new table
                await db.execute(create_sql)

                # 3. Copy data (casting user_id to TEXT)
                cols_str = ", ".join(columns_to_copy)
                await db.execute(
                    f"INSERT INTO {table_name} ({cols_str}) SELECT {cols_str} FROM {temp_table}"
                )

                # 4. Drop old table
                await db.execute(f"DROP TABLE {temp_table}")
                logger.info(f"Migration for {table_name} completed.")

            except Exception as e:
                logger.error(f"Failed to migrate {table_name}: {e}")
                # Try to ensure table exists at least
                await db.execute(create_sql)

        # --- Tables Definition & Migration ---

        # 视频缓存表 (ID is string key, OK)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS video_cache (
                file_id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 用户统计表 (Needs Migration)
        user_stats_cols = [
            "user_id",
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
            "first_use",
            "last_use",
        ]
        await migrate_table_pk_to_text(
            "user_stats",
            """
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id TEXT PRIMARY KEY,
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
            """,
            user_stats_cols,
        )

        # 提醒任务表 (user_id is not PK, just INTEGER -> TEXT compatible usually but strictly better to define as TEXT)
        # But for non-PK columns, SQLite tolerates strings in INTEGER columns unless STRICT.
        # We will leave reminders as is for now to minimize risk, unless issues arise.
        # Actually, let's just make sure new deployment uses TEXT.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                message TEXT NOT NULL,
                trigger_time TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                platform TEXT DEFAULT 'telegram'
            )
        """)

        # 用户设置表 (Needs Migration)
        await migrate_table_pk_to_text(
            "user_settings",
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT PRIMARY KEY,
                auto_translate BOOLEAN DEFAULT 0,
                target_lang TEXT DEFAULT 'zh-CN',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            ["user_id", "auto_translate", "target_lang", "updated_at"],
        )

        # 订阅表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                feed_url TEXT NOT NULL,
                title TEXT,
                last_etag TEXT,
                last_modified TEXT,
                last_entry_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                platform TEXT DEFAULT 'telegram',
                UNIQUE(user_id, feed_url)
            )
        """)

        # 允许用户表 (白名单) (Needs Migration)
        await migrate_table_pk_to_text(
            "allowed_users",
            """
            CREATE TABLE IF NOT EXISTS allowed_users (
                user_id TEXT PRIMARY KEY,
                added_by TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            ["user_id", "added_by", "description", "created_at"],
        )

        # 自选股表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                platform TEXT DEFAULT 'telegram',
                UNIQUE(user_id, stock_code)
            )
        """)

        # 对话历史表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL, -- 'user' or 'model'
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT
            )
        """)

        # 定时任务表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crontab TEXT NOT NULL,
                instruction TEXT NOT NULL,
                user_id TEXT DEFAULT '0',
                platform TEXT DEFAULT 'telegram',
                need_push BOOLEAN DEFAULT 1,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 索引创建
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_active ON scheduled_tasks(is_active)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_history_user_session ON chat_history(user_id, session_id)"
        )

        # 账号管理表 (user_id TEXT)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                service TEXT NOT NULL,
                enc_data TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, service)
            )
        """)

        await db.commit()
    logger.info("Database initialized successfully")
