"""
Chat History Repository
Persist chat messages to SQLite.
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime
import uuid

from .base import get_db

logger = logging.getLogger(__name__)


async def save_message(user_id: int, role: str, content: str, session_id: str) -> bool:
    """保存一条消息"""
    try:
        async with await get_db() as db:
            await db.execute(
                "INSERT INTO chat_history (user_id, role, content, session_id) VALUES (?, ?, ?, ?)",
                (user_id, role, content, session_id),
            )
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Error saving message: {e}")
        return False


async def get_session_messages(
    user_id: int, session_id: str, limit: int = 20
) -> List[Dict]:
    """获取指定会话的历史消息（按时间倒序获取，然后正序返回）"""
    try:
        async with await get_db() as db:
            async with db.execute(
                """
                SELECT role, content 
                FROM chat_history 
                WHERE user_id = ? AND session_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
                """,
                (user_id, session_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()

        messages = []
        for role, content in rows:
            messages.append({"role": role, "parts": [{"text": content}]})

        return messages[::-1]  # Reverse structure to chrono order
    except Exception as e:
        logger.error(f"Error getting session history: {e}")
        return []


async def get_latest_session_id(user_id: int) -> str:
    """获取用户最新的 session_id，如果没有则创建"""
    try:
        async with await get_db() as db:
            async with db.execute(
                "SELECT session_id FROM chat_history WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()

        if row and row[0]:
            return row[0]
        else:
            return str(uuid.uuid4())

    except Exception as e:
        logger.error(f"Error getting latest session: {e}")
        return str(uuid.uuid4())


async def search_messages(
    user_id: int | str,
    keyword: str,
    *,
    limit: int = 20,
    session_id: Optional[str] = None,
) -> List[Dict]:
    """按关键词检索用户对话。"""
    text = str(keyword or "").strip()
    if not text:
        return []
    like_pattern = f"%{text}%"
    try:
        async with await get_db() as db:
            if session_id:
                query = """
                    SELECT role, content, created_at, session_id
                    FROM chat_history
                    WHERE user_id = ? AND session_id = ? AND content LIKE ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """
                params = (str(user_id), str(session_id), like_pattern, max(1, int(limit)))
            else:
                query = """
                    SELECT role, content, created_at, session_id
                    FROM chat_history
                    WHERE user_id = ? AND content LIKE ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """
                params = (str(user_id), like_pattern, max(1, int(limit)))

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        results: List[Dict] = []
        for role, content, created_at, row_session_id in rows:
            results.append(
                {
                    "role": role,
                    "content": content,
                    "created_at": str(created_at),
                    "session_id": str(row_session_id or ""),
                }
            )
        return results
    except Exception as e:
        logger.error(f"Error searching messages: {e}")
        return []


async def get_recent_messages_for_user(
    *,
    user_id: int | str,
    limit: int = 50,
) -> List[Dict]:
    """按时间倒序读取用户最近对话（跨 session）。"""
    try:
        async with await get_db() as db:
            async with db.execute(
                """
                SELECT role, content, created_at, session_id
                FROM chat_history
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (str(user_id), max(1, int(limit))),
            ) as cursor:
                rows = await cursor.fetchall()
        results: List[Dict] = []
        for role, content, created_at, session_id in rows:
            results.append(
                {
                    "role": role,
                    "content": content,
                    "created_at": str(created_at),
                    "session_id": str(session_id or ""),
                }
            )
        return list(reversed(results))
    except Exception as e:
        logger.error(f"Error reading recent messages: {e}")
        return []
