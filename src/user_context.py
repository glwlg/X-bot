"""
用户对话上下文管理模块
使用 PicklePersistence 自动持久化
"""
from typing import Literal
from telegram.ext import ContextTypes

# 每用户保存的最大消息数
MAX_CONTEXT_MESSAGES = 10
CHAT_HISTORY_KEY = "chat_history"


def get_user_context(context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    """
    获取用户的对话上下文
    
    Returns:
        对话历史列表，格式符合 Gemini API 要求
    """
    return context.user_data.get(CHAT_HISTORY_KEY, [])


def add_message(
    context: ContextTypes.DEFAULT_TYPE,
    role: Literal["user", "model"],
    content: str,
) -> None:
    """
    添加一条消息到用户上下文
    
    Args:
        context: Telegram 上下文对象
        role: 消息角色，"user" 或 "model"
        content: 消息内容
    """
    if CHAT_HISTORY_KEY not in context.user_data:
        context.user_data[CHAT_HISTORY_KEY] = []
    
    history = context.user_data[CHAT_HISTORY_KEY]
    history.append({"role": role, "parts": [{"text": content}]})
    
    # 保留最近 N 条
    if len(history) > MAX_CONTEXT_MESSAGES:
        context.user_data[CHAT_HISTORY_KEY] = history[-MAX_CONTEXT_MESSAGES:]


def clear_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    """清除用户的对话上下文"""
    context.user_data[CHAT_HISTORY_KEY] = []


def get_context_length(context: ContextTypes.DEFAULT_TYPE) -> int:
    """获取用户当前上下文的消息数量"""
    return len(get_user_context(context))
