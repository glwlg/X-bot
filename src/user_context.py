"""
用户对话上下文管理模块
保存每个用户最近的对话历史，用于多轮对话
"""
"""
用户对话上下文管理模块
保存每个用户最近的对话历史，用于多轮对话
"""
from typing import Literal
from database import get_chat_history, add_chat_message

# 每用户保存的最大消息数
MAX_CONTEXT_MESSAGES = 10


async def get_user_context(user_id: int) -> list[dict]:
    """
    获取用户的对话上下文
    
    Returns:
        对话历史列表，格式符合 Gemini API 要求
    """
    return await get_chat_history(user_id, limit=MAX_CONTEXT_MESSAGES)


async def add_message(user_id: int, role: Literal["user", "model"], content: str, message_id: int = None) -> None:
    """
    添加一条消息到用户上下文
    
    Args:
        user_id: 用户 ID
        role: 消息角色，"user" 或 "model"
        content: 消息内容
        message_id: Telegram 消息 ID
    """
    await add_chat_message(user_id, role, content, message_id)


async def clear_context(user_id: int) -> None:
    """清除用户的对话上下文"""
    # 目前数据库层未实现删除，暂时留空或后续添加
    # 或者直接在该表插入一条标记？实际上 Gemini 只是读取最近历史。
    # 这里我们暂时不实现真正的清除（或者需要 database.py 支持 clear）
    pass


async def get_context_length(user_id: int) -> int:
    """获取用户当前上下文的消息数量"""
    history = await get_user_context(user_id)
    return len(history)
