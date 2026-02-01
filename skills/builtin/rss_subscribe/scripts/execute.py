from core.platform.models import UnifiedContext
from handlers.subscription_handlers import (
    process_subscribe,
    list_subs_command,
    unsubscribe_command,
    delete_subscription,
    refresh_user_subscriptions,
)


async def execute(ctx: UnifiedContext, params: dict) -> str:
    """执行 RSS 订阅或关键词监控"""
    action = params.get("action", "add")
    # 支持 url 或 keyword 参数
    url = params.get("url") or params.get("keyword", "")

    if action == "refresh":
        msg = await refresh_user_subscriptions(ctx)
        if msg:
            await ctx.reply(msg)
        return "✅ RSS 刷新完成"

    if action == "list":
        result_text = await list_subs_command(ctx)
        return (
            f"✅ 订阅列表已发送。\n[CONTEXT_DATA_ONLY - DO NOT REPEAT]\n{result_text}"
        )

    if action == "remove":
        if url:
            # Direct remove if URL is provided
            user_id = int(ctx.message.user.id)
            success = await delete_subscription(user_id, url)
            if success:
                await ctx.reply(f"🗑️ 已取消订阅：`{url}`")
                return f"✅ 已取消订阅: {url}"
            else:
                await ctx.reply(f"❌ 取消失败，未找到该订阅：`{url}`")
                return f"❌ 取消失败: {url}"
        else:
            # Interactive remove
            await unsubscribe_command(ctx)
            return "✅ 进入取消订阅交互模式"

    # Default: Add
    if not url:
        await ctx.reply(
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
    if await process_subscribe(ctx, url):
        return f"✅ 订阅成功: {url}"
    else:
        return f"❌ 订阅失败: {url}"
