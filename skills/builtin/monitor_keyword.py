"""
监控关键词 Skill - 监控新闻中的指定关键词
"""
from telegram import Update
from telegram.ext import ContextTypes

from utils import smart_reply_text


SKILL_META = {
    "name": "monitor_keyword",
    "description": "监控新闻中的指定关键词，有新消息时推送",
    "triggers": ["监控", "monitor", "关注新闻", "跟踪", "追踪"],
    "params": {
        "keyword": {
            "type": "str",
            "description": "要监控的关键词"
        }
    },
    "version": "1.0.0",
    "author": "system"
}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    """执行关键词监控"""
    keyword = params.get("keyword", "")
    
    if not keyword:
        await smart_reply_text(update,
            "🔍 **监控关键词**\n\n"
            "请告诉我要监控的关键词，例如：\n"
            "• 监控 AI\n"
            "• 帮我追踪新能源相关新闻"
        )
        return
    
    # 委托给现有逻辑
    from handlers.subscription_handlers import process_monitor
    await process_monitor(update, context, keyword)
