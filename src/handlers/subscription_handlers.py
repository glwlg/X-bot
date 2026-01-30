"""
订阅与监控功能 handlers
"""
import re
import logging
import urllib.parse
import feedparser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from core.config import WAITING_FOR_MONITOR_KEYWORD, WAITING_FOR_SUBSCRIBE_URL
from repositories import (
    get_user_subscriptions, add_subscription, delete_subscription,
    delete_subscription_by_id,
)
from stats import increment_stat
from core.platform.models import UnifiedContext
from .base_handlers import check_permission_unified


async def subscribe_command(ctx: UnifiedContext) -> int:
    """处理 /subscribe 命令，支持交互式输入"""
    if not await check_permission_unified(ctx):
        return ConversationHandler.END

    if not ctx.platform_ctx:
         return ConversationHandler.END

    args = ctx.platform_ctx.args
    if args:
        await process_subscribe(ctx, args[0])
        return ConversationHandler.END
        
    await ctx.reply(
        "📢 **订阅 RSS 源**\n\n"
        "请发送您想订阅的 RSS 链接。\n"
        "Bot 将每 30 分钟检查更新。\n\n"
        "示例：\n"
        "https://feeds.feedburner.com/PythonInsider\n\n"
        "发送 /cancel 取消。"
    )
    return WAITING_FOR_SUBSCRIBE_URL


async def handle_subscribe_input(ctx: UnifiedContext) -> int:
    """处理 RSS 链接的输入"""
    url = ctx.message.text
    if not url:
        await ctx.reply("请发送有效的链接。")
        return WAITING_FOR_SUBSCRIBE_URL
        
    success = await process_subscribe(ctx, url)
    if success:
        return ConversationHandler.END
    else:
        return WAITING_FOR_SUBSCRIBE_URL


async def process_subscribe(ctx: UnifiedContext, url: str) -> bool:
    """实际处理订阅逻辑"""
    user_id = ctx.message.user.id
    
    if not url.startswith("http"):
        await ctx.reply("❌ 请输入有效的 HTTP/HTTPS 链接。")
        return False
        
    try:
        msg = await ctx.reply("🔍 正在验证 RSS 源...")
        feed = feedparser.parse(url)
             
        title = feed.feed.get("title", url)
        if not title:
             title = url
             
        try:
            await add_subscription(user_id, url, title)
            await ctx.edit_message(msg.message_id, f"✅ **订阅成功！**\n\n源：{title}\nBot 将每 30 分钟检查一次更新。")
            try:
                uid_int = int(user_id)
                await increment_stat(uid_int, "subscriptions_added")
            except:
                pass
            return True
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                await ctx.edit_message(msg.message_id, "⚠️ 您已经订阅过这个源了。")
                return True
            else:
                await ctx.edit_message(msg.message_id, f"❌ 订阅失败: {e}")
                return False
                 
    except Exception as e:
        logger.error(f"Subscribe error: {e}")
        await ctx.edit_message(msg.message_id, "❌ 无法访问该 RSS 源。")
        return False


async def unsubscribe_command(ctx: UnifiedContext) -> None:
    """处理 /unsubscribe 命令"""
    if not await check_permission_unified(ctx):
        return

    user_id = ctx.message.user.id
    args = ctx.platform_ctx.args if ctx.platform_ctx else []
    
    if args:
        url = args[0]
        await delete_subscription(user_id, url)
        await ctx.reply(f"🗑️ 已取消订阅：`{url}`")
        return
    
    subs = await get_user_subscriptions(user_id)
    
    if not subs:
        await ctx.reply("📭 您当前没有订阅任何内容。")
        return
    
    keyboard = []
    for sub in subs:
        title = sub["title"] or sub["feed_url"][:30]
        keyboard.append([InlineKeyboardButton(f"❌ {title}", callback_data=f"unsub_{sub['id']}")])
    
    keyboard.append([InlineKeyboardButton("🚫 取消", callback_data="unsub_cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await ctx.reply("📋 **请选择要取消的订阅**：", reply_markup=reply_markup)


async def handle_unsubscribe_callback(ctx: UnifiedContext) -> None:
    """处理取消订阅按钮回调"""
    # Legacy fallback
    query = ctx.platform_event.callback_query
    await query.answer()
    
    data = query.data
    user_id = ctx.message.user.id
    
    if data == "unsub_cancel":
        await ctx.edit_message(query.message.message_id, "👌 已取消操作。")
        return
    
    try:
        sub_id = int(data.replace("unsub_", ""))
    except ValueError:
        await ctx.edit_message(query.message.message_id, "❌ 无效的操作。")
        return
    
    success = await delete_subscription_by_id(sub_id, user_id)
    
    if success:
        await ctx.edit_message(query.message.message_id, "✅ 订阅已取消。")
    else:
        await ctx.edit_message(query.message.message_id, "❌ 取消失败，订阅可能已不存在。")


async def monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /monitor 命令，支持交互式输入"""
    if not await check_permission(update):
        return ConversationHandler.END

    args = context.args
    if args:
        await process_monitor(update, context, " ".join(args))
        return ConversationHandler.END
        
    await smart_reply_text(update,
        "🔍 **监控关键词**\n\n"
        "请发送您想监控的关键词。\n"
        "Bot 将通过 Google News 监控并在有新内容时通知您。\n\n"
        "示例：\n"
        "• Python 教程\n"
        "• 人工智能\n\n"
        "发送 /cancel 取消。"
    )
    return WAITING_FOR_MONITOR_KEYWORD


async def handle_monitor_input(ctx: UnifiedContext) -> int:
    """处理监控关键词的输入"""
    keyword = ctx.message.text
    if not keyword:
        await ctx.reply("请发送有效文本。")
        return WAITING_FOR_MONITOR_KEYWORD
        
    success = await process_monitor(ctx, keyword)
    if success:
        return ConversationHandler.END
    else:
        return WAITING_FOR_MONITOR_KEYWORD


async def process_monitor(ctx: UnifiedContext, keyword: str) -> bool:
    """实际处理监控逻辑，支持多关键词"""
    user_id = ctx.message.user.id
    
    keywords = re.split(r'[、,，]+', keyword.strip())
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
        "**监控设置完成！**\n\n" +
        "\n".join(result_parts) +
        "\n\n来源：Google News\nBot 将每 30 分钟推送相关新闻。"
    )
    
    await ctx.edit_message(msg.message_id, result_msg)
    return len(success_list) > 0 or len(existed_list) > 0


async def list_subs_command(ctx: UnifiedContext) -> None:
    """处理 /list_subs 命令"""
    if not await check_permission_unified(ctx):
        return

    user_id = ctx.message.user.id
    
    subs = await get_user_subscriptions(user_id)
    
    if not subs:
        await ctx.reply("📭 您当前没有订阅任何 RSS 源。")
        return
        
    msg = "📋 **您的订阅列表**：\n\n"
    for sub in subs:
        title = sub["title"]
        url = sub["feed_url"]
        msg += f"• [{title}]({url})\n\n"
             
    msg += "也可以直接点击下方按钮取消订阅："
    
    keyboard = []
    temp_row = []
    for sub in subs:
        short_title = sub["title"][:10] + ".." if len(sub["title"]) > 10 else sub["title"]
        btn = InlineKeyboardButton(f"❌ {short_title}", callback_data=f"unsub_{sub['id']}")
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
    
    # 防止频繁调用 (简单防刷，这里可选)
    # 比如检查 timer
    
    # await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    # Not creating separate action for now or fallback
    if ctx.platform_ctx:
        try:
           await ctx.platform_ctx.bot.send_chat_action(chat_id=ctx.message.chat.id, action="typing")
        except:
           pass
    
    from core.scheduler import trigger_manual_rss_check
    result_text = await trigger_manual_rss_check(ctx.platform_ctx, user_id) if ctx.platform_ctx else "Platform not supported"
    
    if result_text:
        return result_text
    else:
        return "✅ 检查完成，您订阅的内容暂时没有更新。"
