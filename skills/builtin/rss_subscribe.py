"""
RSS 订阅 Skill - 订阅 RSS/Atom 源
"""
from telegram import Update
from telegram.ext import ContextTypes

from utils import smart_reply_text


SKILL_META = {
    "name": "rss_subscribe",
    "description": "订阅 RSS/Atom 源，有更新时推送",
    "triggers": ["订阅", "subscribe", "rss", "atom", "feed", "列表", "取消"],
    "params": {
        "action": {
            "type": "str",
            "description": "操作类型：add (添加), list (列表), remove (删除), refresh (刷新)",
            "default": "add",
            "enum": ["add", "list", "remove", "refresh"]
        },
        "url": {
            "type": "str",
            "description": "RSS 源的 URL（添加或删除时需要）"
        }
    },
    "version": "1.2.0",
    "author": "system"
}


async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> str:
    """执行 RSS 订阅"""
    action = params.get("action", "add")
    url = params.get("url", "")
    
    from handlers.subscription_handlers import (
        process_subscribe, list_subs_command, 
        unsubscribe_command, delete_subscription, 
        refresh_user_subscriptions
    )
    
    if action == "refresh":
        msg = await refresh_user_subscriptions(update, context)
        if msg:
            await smart_reply_text(update, msg)
        return "✅ RSS 刷新完成"
    
    if action == "list":
        result_text = await list_subs_command(update, context)
        return f"✅ 订阅列表已发送。\n[CONTEXT_DATA_ONLY - DO NOT REPEAT]\n{result_text}"

    if action == "remove":
        if url:
            # Direct remove if URL is provided
            user_id = update.effective_user.id
            success = await delete_subscription(user_id, url)
            if success:
                await smart_reply_text(update, f"🗑️ 已取消订阅：`{url}`")
                return f"✅ 已取消订阅: {url}"
            else:
                 await smart_reply_text(update, f"❌ 取消失败，未找到该订阅：`{url}`")
                 return f"❌ 取消失败: {url}"
        else:
             # Interactive remove
             await unsubscribe_command(update, context)
             return "✅ 进入取消订阅交互模式"
    
    # Default: Add
    if not url:
        await smart_reply_text(update,
            "📢 **订阅 RSS**\n\n"
            "请提供 RSS 源的链接，例如：\n"
            "• 订阅 https://example.com/feed.xml\n"
            "• 帮我订阅这个 RSS https://...\n\n"
            "或者：\n"
            "• 订阅列表\n"
            "• 取消订阅"
        )
        return "❌ 未提供 URL"
    
    # 委托给现有逻辑
    if await process_subscribe(update, context, url):
        return f"✅ 订阅成功: {url}"
    else:
        return f"❌ 订阅失败: {url}"

