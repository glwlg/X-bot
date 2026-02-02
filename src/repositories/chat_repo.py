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
