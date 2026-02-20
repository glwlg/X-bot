"""
用户对话上下文管理模块
使用 SQLite 持久化 (替代 PicklePersistence 的 chat_history)
"""

import uuid
import logging
from typing import Literal
from telegram.ext import ContextTypes

from repositories.chat_repo import (
    save_message,
    get_session_messages,
    get_latest_session_id,
)

logger = logging.getLogger(__name__)

SESSION_ID_KEY = "current_session_id"


from typing import Union, Any
from core.platform.models import UnifiedContext


async def get_or_create_session_id(
    context: Union[ContextTypes.DEFAULT_TYPE, UnifiedContext], user_id: int
) -> str:
    """获取当前 Session ID，如果内存没有，尝试从 DB 获取最新的"""
    if SESSION_ID_KEY in context.user_data:
        return context.user_data[SESSION_ID_KEY]

    # 从 DB 获取
    session_id = await get_latest_session_id(user_id)
    context.user_data[SESSION_ID_KEY] = session_id
    return session_id


async def get_user_context(
    context: Union[ContextTypes.DEFAULT_TYPE, UnifiedContext], user_id: int
) -> list[dict]:
    """
    获取用户的对话上下文 (Async)

    Returns:
        对话历史列表，格式符合 Gemini API 要求
    """
    session_id = await get_or_create_session_id(context, user_id)
    # 限制最近 20 条，避免 token 过长
    return await get_session_messages(user_id, session_id, limit=20)


async def add_message(
    context: Union[ContextTypes.DEFAULT_TYPE, UnifiedContext],
    user_id: int,
    role: Literal["user", "model"],
    content: str,
) -> None:
    """
    添加一条消息到用户上下文 (Async)
    """
    session_id = await get_or_create_session_id(context, user_id)
    await save_message(user_id, role, content, session_id)


def clear_context(context: Union[ContextTypes.DEFAULT_TYPE, UnifiedContext]) -> None:
    """
    清除用户的对话上下文 (开启新会话)
    不删除历史记录，只是生成新的 session_id
    """
    new_session_id = str(uuid.uuid4())
    context.user_data[SESSION_ID_KEY] = new_session_id
    logger.info(f"Started new session: {new_session_id}")


async def get_context_length(
    context: Union[ContextTypes.DEFAULT_TYPE, UnifiedContext], user_id: int
) -> int:
    """获取用户当前上下文的消息数量"""
    history = await get_user_context(context, user_id)
    return len(history)
