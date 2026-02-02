"""
RSS Subscription and Monitoring Skill Script
"""

import re
import logging
import urllib.parse
import feedparser
import asyncio
import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from repositories import (
    get_user_subscriptions,
    add_subscription,
    delete_subscription,
    delete_subscription_by_id,
)
from stats import increment_stat
from core.platform.models import UnifiedContext

logger = logging.getLogger(__name__)


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
            # Note: Interactive UI usually initiated by handler, but if skill called via AI,
            # we might want to show the interactive menu too.
            await show_unsubscribe_menu(ctx)
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


def register_handlers(adapter_manager):
    """注册 RSS 相关的 Command 和 Callback"""
    from core.config import is_user_allowed

    # 封装 command handler 以检查权限
    async def cmd_subscribe(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        # Handle args vs interactive
        # Handle args vs interactive
        args = []
        if ctx.message.text:
            parts = ctx.message.text.split()
            if len(parts) > 1:
                args = parts[1:]

        if args:
            await process_subscribe(ctx, args[0])
        else:
            # Interactive / prompt mode - simple implementation for now
            # Real conversation handler integration is harder dynamically.
            # But we can just ask for input or show help.
            # Since simple state machine is in main.py, moving it here is tricky without full refactor.
            # For now, let's keep simple command support.
            await ctx.reply("请使用: /subscribe <URL>")

    async def cmd_monitor(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        args = []
        if ctx.message.text:
            parts = ctx.message.text.split()
            if len(parts) > 1:
                args = parts[1:]

        if args:
            await process_monitor(ctx, " ".join(args))
        else:
            await ctx.reply("请使用: /monitor <关键词>")

    async def cmd_list_subs(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        await list_subs_command(ctx)

    async def cmd_unsubscribe(ctx):
        if not await is_user_allowed(ctx.message.user.id):
            return
        args = []
        if ctx.message.text:
            parts = ctx.message.text.split()
            if len(parts) > 1:
                args = parts[1:]

        if args:
            await delete_subscription(ctx.message.user.id, args[0])
            await ctx.reply(f"🗑️ 已取消订阅：`{args[0]}`")
        else:
            await show_unsubscribe_menu(ctx)

    adapter_manager.on_command("subscribe", cmd_subscribe)
    adapter_manager.on_command("monitor", cmd_monitor)
    adapter_manager.on_command("list_subs", cmd_list_subs)
    adapter_manager.on_command("unsubscribe", cmd_unsubscribe)

    # Callbacks
    adapter_manager.on_callback_query("^unsub_", handle_unsubscribe_callback)


async def fetch_feed_safe(url: str):
    """Safely fetch and parse RSS feed asynchronously"""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.content

        loop = asyncio.get_running_loop()
        # Parse content in thread pool
        return await loop.run_in_executor(None, feedparser.parse, content)


async def process_subscribe(ctx: UnifiedContext, url: str) -> bool:
    """实际处理订阅逻辑"""
    try:
        user_id = int(ctx.message.user.id)
    except (ValueError, TypeError):
        user_id = ctx.message.user.id
        logger.warning(f"Failed to cast user_id {user_id} to int")

    if not url.startswith("http"):
        # 尝试作为关键词处理 (集成 Monitor 功能)
        logger.info(f"Input '{url}' is not a URL, treating as keyword for monitor.")

        # 使用 Google News 搜索该关键词
        keywords = re.split(r"[、,，]+", url.strip())
        keywords = [k.strip() for k in keywords if k.strip()]

        if not keywords:
            await ctx.reply("❌ 请输入有效的 HTTP/HTTPS 链接或监控关键词。")
            return False

        # 如果是多个关键词，直接调用 process_monitor 批量处理
        return await process_monitor(ctx, url)

    try:
        msg = await ctx.reply("🔍 正在验证 RSS 源...")

        # Use safe async fetch
        try:
            feed = await fetch_feed_safe(url)
        except Exception as e:
            logger.error(f"Feed fetch failed: {e}")
            await ctx.edit_message(
                getattr(msg, "message_id", getattr(msg, "id", None)),
                f"❌ 无法连接到 RSS 源: {e}",
            )
            return False

        title = feed.feed.get("title", url)
        if not title:
            title = url

        try:
            platform = ctx.message.platform if ctx.message.platform else "telegram"
            await add_subscription(user_id, url, title, platform=platform)
            await ctx.edit_message(
                getattr(msg, "message_id", getattr(msg, "id", None)),
                f"✅ **订阅成功！**\n\n源：{title}\nBot 将每 30 分钟检查一次更新。",
            )
            try:
                uid_int = int(user_id)
                await increment_stat(uid_int, "subscriptions_added")
            except:
                pass
            return True
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                await ctx.edit_message(
                    getattr(msg, "message_id", getattr(msg, "id", None)),
                    "⚠️ 您已经订阅过这个源了。",
                )
                return True
            else:
                await ctx.edit_message(
                    getattr(msg, "message_id", getattr(msg, "id", None)),
                    f"❌ 订阅失败: {e}",
                )
                return False

    except Exception as e:
        logger.error(f"Subscribe error: {e}")
        await ctx.edit_message(
            getattr(msg, "message_id", getattr(msg, "id", None)),
            "❌ 无法访问该 RSS 源。",
        )
        return False


async def process_monitor(ctx: UnifiedContext, keyword: str) -> bool:
    """实际处理监控逻辑，支持多关键词"""
    user_id = ctx.message.user.id

    keywords = re.split(r"[、,，]+", keyword.strip())
    keywords = [k.strip() for k in keywords if k.strip()]

    if not keywords:
        await ctx.reply("❌ 请输入有效的关键词。")
        return False

    msg = await ctx.reply(f"🔍 正在配置 {len(keywords)} 个关键词监控...")

    success_list = []
    failed_list = []
    existed_list = []

    for kw in keywords:
        encoded_keyword = urllib.parse.quote(kw)
        rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        title = f"监控: {kw}"

        try:
            await add_subscription(user_id, rss_url, title)
            success_list.append(kw)
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                existed_list.append(kw)
            else:
                logger.error(f"Monitor error for '{kw}': {e}")
                failed_list.append(kw)

    result_parts = []
    if success_list:
        result_parts.append(f"✅ 已添加监控：{', '.join(success_list)}")
    if existed_list:
        result_parts.append(f"⚠️ 已存在：{', '.join(existed_list)}")
    if failed_list:
        result_parts.append(f"❌ 添加失败：{', '.join(failed_list)}")

    result_msg = (
        "**监控设置完成！**\n\n"
        + "\n".join(result_parts)
        + "\n\n来源：Google News\nBot 将每 30 分钟推送相关新闻。"
    )

    await ctx.edit_message(
        getattr(msg, "message_id", getattr(msg, "id", None)), result_msg
    )
    return len(success_list) > 0 or len(existed_list) > 0


async def list_subs_command(ctx: UnifiedContext) -> str:
    """处理 /list_subs 命令"""
    # Note: Permission check removed from here, should be done by caller/agent

    user_id = ctx.message.user.id

    subs = await get_user_subscriptions(user_id)

    if not subs:
        await ctx.reply("📭 您当前没有订阅任何 RSS 源。")
        return "📭 无订阅"

    msg = "📋 **您的订阅列表**：\n\n"
    for sub in subs:
        title = sub["title"]
        url = sub["feed_url"]
        msg += f"• [{title}]({url})\n\n"

    msg += "也可以直接点击下方按钮取消订阅："

    keyboard = []
    temp_row = []
    for sub in subs:
        short_title = (
            sub["title"][:10] + ".." if len(sub["title"]) > 10 else sub["title"]
        )
        btn = InlineKeyboardButton(
            f"❌ {short_title}", callback_data=f"unsub_{sub['id']}"
        )
        temp_row.append(btn)

        if len(temp_row) == 2:
            keyboard.append(temp_row)
            temp_row = []

    if temp_row:
        keyboard.append(temp_row)

    reply_markup = InlineKeyboardMarkup(keyboard)
    await ctx.reply(msg, reply_markup=reply_markup)
    return msg


async def refresh_user_subscriptions(ctx: UnifiedContext) -> str:
    """
    [Tool] 手动刷新当前用户的订阅
    """
    user_id = ctx.message.user.id

    if ctx.platform_ctx:
        try:
            await ctx.platform_ctx.bot.send_chat_action(
                chat_id=ctx.message.chat.id, action="typing"
            )
        except:
            pass

    from core.scheduler import trigger_manual_rss_check

    result_text = (
        await trigger_manual_rss_check(ctx.platform_ctx, user_id)
        if ctx.platform_ctx
        else "Platform not supported"
    )

    if result_text:
        return result_text
    else:
        return "✅ 检查完成，您订阅的内容暂时没有更新。"


async def show_unsubscribe_menu(ctx: UnifiedContext) -> None:
    """显示取消订阅菜单"""
    user_id = ctx.message.user.id
    subs = await get_user_subscriptions(user_id)

    if not subs:
        await ctx.reply("📭 您当前没有订阅任何内容。")
        return

    keyboard = []
    for sub in subs:
        title = sub["title"] or sub["feed_url"][:30]
        keyboard.append(
            [InlineKeyboardButton(f"❌ {title}", callback_data=f"unsub_{sub['id']}")]
        )

    keyboard.append([InlineKeyboardButton("🚫 取消", callback_data="unsub_cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await ctx.reply("📋 **请选择要取消的订阅**：", reply_markup=reply_markup)


async def handle_unsubscribe_callback(ctx: UnifiedContext) -> None:
    """处理取消订阅按钮回调"""
    data = ctx.callback_data
    if not data:
        return

    await ctx.answer_callback()

    if data == "unsub_cancel":
        await ctx.reply("👌 已取消操作。")
        return

    try:
        sub_id = int(data.replace("unsub_", ""))
        user_id = ctx.callback_user_id
    except ValueError:
        await ctx.reply("❌ 无效的操作。")
        return

    success = await delete_subscription_by_id(sub_id, user_id)

    if success:
        await ctx.reply("✅ 订阅已取消。")
    else:
        await ctx.reply("❌ 取消失败，订阅可能已不存在。")
