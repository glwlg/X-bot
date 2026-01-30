SKILL_META = {
    "name": "keyword_monitor",
    "description": "管理关键词监控，支持添加、列出和删除监控。修复Message对象属性访问错误。",
    "version": "1.0.1",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "remove"],
                "description": "操作类型：add添加监控，list列出监控，remove删除监控"
            },
            "keyword": {
                "type": "string",
                "description": "要监控或取消监控的关键词"
            }
        },
        "required": ["action"]
    }
}

from core.platform.models import UnifiedContext
import urllib.parse

async def execute(ctx: UnifiedContext, params: dict) -> str:
    """执行关键词监控"""
    action = params.get("action", "add")
    keyword = params.get("keyword", "")
    
    from handlers.subscription_handlers import process_monitor, list_subs_command, unsubscribe_command
    from repositories import delete_subscription
    
    if action == "list":
        result_text = await list_subs_command(ctx)
        return f"✅ 监控列表已发送。\n[CONTEXT_DATA_ONLY - DO NOT REPEAT]\n{result_text}"

    if action == "remove":
        if keyword:
            # Try to construct the RSS URL for Google News to delete it
            encoded_keyword = urllib.parse.quote(keyword.strip())
            rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
            
            # 安全获取user_id，兼容不同的消息对象结构
            try:
                user_id = int(ctx.message.user.id)
            except AttributeError:
                # 尝试从ctx直接获取用户信息
                try:
                    user_id = int(ctx.user.id)
                except AttributeError:
                    # 尝试从原始update获取
                    try:
                        user_id = int(ctx.raw_message.from_user.id)
                    except AttributeError:
                        return "❌ 无法获取用户ID"
            
            success = await delete_subscription(user_id, rss_url)
            if success:
                await ctx.reply(f"🗑️ 已取消监控：{keyword}")
                return f"✅ 已取消监控: {keyword}"
            else:
                # Fallback to interactive unsubscribe if direct match fails
                await unsubscribe_command(ctx)
                return "✅ 进入取消交互模式 (直接匹配失败)"
        else:
            await unsubscribe_command(ctx)
            return "✅ 进入取消交互模式"

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