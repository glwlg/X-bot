"""
RSS 订阅 Skill - 订阅 RSS/Atom 源
"""
from telegram import Update
from telegram.ext import ContextTypes

from utils import smart_reply_text


SKILL_META = {
    "name": "rss_subscribe",
    "description": "订阅 RSS/Atom 源，有更新时推送",
    "triggers": ["订阅", "subscribe", "rss", "atom", "feed"],
    "params": {
        "url": {
            "type": "str",
            "description": "RSS 源的 URL"
        }
    },
    "version": "1.0.0",
    "author": "system"
}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    """执行 RSS 订阅"""
    url = params.get("url", "")
    
    if not url:
        await smart_reply_text(update,
            "📢 **订阅 RSS**\n\n"
            "请提供 RSS 源的链接，例如：\n"
            "• 订阅 https://example.com/feed.xml\n"
            "• 帮我订阅这个 RSS https://..."
        )
        return
    
    # 委托给现有逻辑
    from handlers.subscription_handlers import process_subscribe
    await process_subscribe(update, context, url)
