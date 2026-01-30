from core.platform.models import UnifiedContext
from utils import smart_reply_text
from handlers.subscription_handlers import process_monitor, list_subs_command, unsubscribe_command
from repositories import delete_subscription
import urllib.parse

async def execute(ctx: UnifiedContext, params: dict) -> str:
    """执行关键词监控"""
    action = params.get("action", "add")
    keyword = params.get("keyword", "")
    
    # helper to check basic perms (though usually handled by handler, good practice if reused)
    # But here we rely on the implementation in handlers.
    
    from handlers.subscription_handlers import process_monitor, list_subs_command, unsubscribe_command
    from repositories import delete_subscription
    import urllib.parse
    
    if action == "list":
        result_text = await list_subs_command(ctx)
        return f"✅ 监控列表已发送。\n[CONTEXT_DATA_ONLY - DO NOT REPEAT]\n{result_text}"


    if action == "remove":
        if keyword:
            # Try to construct the RSS URL for Google News to delete it
            # This logic mimics process_monitor's URL construction
            # But process_monitor supports multiple keywords. Here we try best effort single.
            # If complex match needed, user should use interactive /unsubscribe
            encoded_keyword = urllib.parse.quote(keyword.strip())
            rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
            
            # Using user_id from update
            user_id = int(ctx.message.user.id)
            success = await delete_subscription(user_id, rss_url)
            if success:
                await ctx.reply(f"🗑️ 已取消监控：{keyword}")
                return f"✅ 已取消监控: {keyword}"
            else:
                # Fallback to interactive unsubscribe if direct match fails or user wants selection
                 await unsubscribe_command(ctx)
                 return "✅ 进入取消交互模式 (直接匹配失败)"
        else:
             await unsubscribe_command(ctx)
             return "✅ 进入取消交互模式"
        return

    # Default: Add
    if not keyword:
        await ctx.reply(
            "🔍 **监控关键词**\n\n"
            "请告诉我要监控的关键词，例如：\n"
            "• 监控 AI\n"
            "• 帮我追踪新能源相关新闻\n\n"
            "或者：\n"
            "• 监控列表\n"
            "• 取消监控 AI"
        )
        return "❌ 未提供关键词"
    
    # 委托给现有逻辑
    if await process_monitor(ctx, keyword):
        return f"✅ 监控添加成功: {keyword}"
    else:
        return f"❌ 监控添加失败: {keyword}"

