"""
监控关键词 Skill - 监控新闻中的指定关键词
"""
from telegram import Update
from telegram.ext import ContextTypes

from utils import smart_reply_text


SKILL_META = {
    "name": "monitor_keyword",
    "description": "监控新闻中的指定关键词，有新消息时推送",
    "triggers": ["监控", "monitor", "关注新闻", "跟踪", "追踪", "列表", "取消"],
    "params": {
        "action": {
            "type": "str",
            "description": "操作类型：add (添加), list (列表), remove (删除)",
            "default": "add",
            "enum": ["add", "list", "remove"]
        },
        "keyword": {
            "type": "str",
            "description": "要监控的关键词（添加或删除时需要）"
        }
    },
    "version": "1.1.0",
    "author": "system"
}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    """执行关键词监控"""
    action = params.get("action", "add")
    keyword = params.get("keyword", "")
    
    # helper to check basic perms (though usually handled by handler, good practice if reused)
    # But here we rely on the implementation in handlers.
    
    from handlers.subscription_handlers import process_monitor, list_subs_command, unsubscribe_command
    from repositories import delete_subscription
    import urllib.parse
    
    if action == "list":
        await list_subs_command(update, context)
        return

    if action == "remove":
        if keyword:
            # Try to construct the RSS URL for Google News to delete it
            # This logic mimics process_monitor's URL construction
            # But process_monitor supports multiple keywords. Here we try best effort single.
            # If complex match needed, user should use interactive /unsubscribe
            encoded_keyword = urllib.parse.quote(keyword.strip())
            rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
            
            # Using user_id from update
            user_id = update.effective_user.id
            success = await delete_subscription(user_id, rss_url)
            if success:
                await smart_reply_text(update, f"🗑️ 已取消监控：{keyword}")
            else:
                # Fallback to interactive unsubscribe if direct match fails or user wants selection
                 await unsubscribe_command(update, context)
        else:
             await unsubscribe_command(update, context)
        return

    # Default: Add
    if not keyword:
        await smart_reply_text(update,
            "🔍 **监控关键词**\n\n"
            "请告诉我要监控的关键词，例如：\n"
            "• 监控 AI\n"
            "• 帮我追踪新能源相关新闻\n\n"
            "或者：\n"
            "• 监控列表\n"
            "• 取消监控 AI"
        )
        return
    
    # 委托给现有逻辑
    await process_monitor(update, context, keyword)
