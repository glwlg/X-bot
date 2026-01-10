import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import (
    WAITING_FOR_REMIND_INPUT,
    WAITING_FOR_MONITOR_KEYWORD,
    WAITING_FOR_SUBSCRIBE_URL,
)
from database import (
    get_user_subscriptions, add_subscription, delete_subscription,
    get_user_settings, set_translation_mode
)
from stats import get_user_stats_text
from .base_handlers import check_permission
from utils import smart_edit_text, smart_reply_text

logger = logging.getLogger(__name__)

# --- Stats ---

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /stats 命令"""
    if not await check_permission(update):
        return

    user_id = update.effective_user.id
    stats_text = await get_user_stats_text(user_id)
    
    stats_text = await get_user_stats_text(user_id)
    
    await smart_reply_text(update, stats_text)


# --- Reminder ---

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /remind 命令，支持交互式输入"""
    if not await check_permission(update):
        return ConversationHandler.END

    args = context.args
    # 如果有参数，直接执行逻辑
    if args and len(args) >= 2:
        await process_remind(update, context, args[0], " ".join(args[1:]))
        return ConversationHandler.END
        
    # 没有参数，提示输入
    await smart_reply_text(update,
        "⏰ **设置定时提醒**\n\n"
        "请发送您想要的提醒时间和内容。\n"
        "格式：`&lt;时间&gt; &lt;内容&gt;`\n\n"
        "示例：\n"
        "• 10m 喝水\n"
        "• 1h30m 开会\n"
        "• 20s 测试一下\n\n"
        "发送 /cancel 取消。"
    )
    return WAITING_FOR_REMIND_INPUT


async def handle_remind_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理提醒的交互式输入"""
    text = update.message.text
    if not text:
        await update.message.reply_text("请发送有效文本。")
        return WAITING_FOR_REMIND_INPUT
        
    parts = text.strip().split(" ", 1)
    if len(parts) < 2:
        await update.message.reply_text(
            "⚠️ 格式不正确。请同时提供时间和内容，用空格分开。\n"
            "例如：10m 喝水"
        )
        return WAITING_FOR_REMIND_INPUT
        
    success = await process_remind(update, context, parts[0], parts[1])
    if success:
        return ConversationHandler.END
    else:
        return WAITING_FOR_REMIND_INPUT


async def process_remind(update: Update, context: ContextTypes.DEFAULT_TYPE, time_str: str, message: str) -> bool:
    """实际处理提醒逻辑（复用）"""
    
    # 解析时间
    import re
    import datetime
    
    # 简单的正则解析：支持单个单位 (e.g. 10m) 或组合 (e.g. 1h30m)
    matches = re.findall(r"(\d+)([smhd])", time_str.lower())
    
    if not matches:
        await smart_reply_text(update, "❌ 时间格式错误。请使用如 10m, 1h, 30s 等格式。")
        return False
        return False
        
    delta_seconds = 0
    for value, unit in matches:
        value = int(value)
        if unit == 's':
            delta_seconds += value
        elif unit == 'm':
            delta_seconds += value * 60
        elif unit == 'h':
            delta_seconds += value * 3600
        elif unit == 'd':
            delta_seconds += value * 86400
            
    if delta_seconds <= 0:
        await smart_reply_text(update, "❌ 时间必须大于 0。")
        return False
        
    trigger_time = datetime.datetime.now().astimezone() + datetime.timedelta(seconds=delta_seconds)
    
    # 调度任务
    from scheduler import schedule_reminder
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await schedule_reminder(context.job_queue, user_id, chat_id, message, trigger_time)
    
    # 格式化显示的触发时间 (HH:MM:SS)
    display_time = trigger_time.strftime("%H:%M:%S")
    if delta_seconds > 86400:
        display_time = trigger_time.strftime("%Y-%m-%d %H:%M:%S")
        
    await smart_reply_text(update,
        f"👌 已设置提醒：{message}\n"
        f"⏰ 将在 {display_time} 提醒你。"
    )
    # 统计
    from stats import increment_stat
    await increment_stat(user_id, "reminders_set")
    return True

# --- Translation ---

async def toggle_translation_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /translate 命令，切换沉浸式翻译模式"""
    if not await check_permission(update):
        return

    user_id = update.effective_user.id
    
    # 获取当前状态
    settings = await get_user_settings(user_id)
    current_status = settings.get("auto_translate", 0)
    
    # 切换状态
    new_status = not current_status
    await set_translation_mode(user_id, new_status)
    
    if new_status:
        await smart_reply_text(update,
            "🌍 **沉浸式翻译模式：已开启**\n\n"
            "现在发送任何文本消息，我都会为您自动翻译。\n"
            "• 外语 -> 中文\n"
            "• 中文 -> 英文\n\n"
            "再次输入 /translate 可关闭。"
        )
    else:
        await smart_reply_text(update,
            "🚫 **沉浸式翻译模式：已关闭**\n\n"
            "已恢复正常 AI 助手模式。"
        )

# --- Subscription / Monitor ---

async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /subscribe 命令，支持交互式输入"""
    if not await check_permission(update):
        return ConversationHandler.END

    args = context.args
    if args:
        await process_subscribe(update, context, args[0])
        return ConversationHandler.END
        
    # 无参数，提示输入
    # 无参数，提示输入
    await smart_reply_text(update,
        "📢 **订阅 RSS 源**\n\n"
        "请发送您想订阅的 RSS 链接。\n"
        "Bot 将每 30 分钟检查更新。\n\n"
        "示例：\n"
        "https://feeds.feedburner.com/PythonInsider\n\n"
        "发送 /cancel 取消。"
    )
    return WAITING_FOR_SUBSCRIBE_URL


async def handle_subscribe_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 RSS 链接的输入"""
    url = update.message.text
    if not url:
        await update.message.reply_text("请发送有效的链接。")
        return WAITING_FOR_SUBSCRIBE_URL
        
    success = await process_subscribe(update, context, url)
    if success:
        return ConversationHandler.END
    else:
        return WAITING_FOR_SUBSCRIBE_URL


async def process_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> bool:
    """实际处理订阅逻辑"""
    user_id = update.effective_user.id
    
    # 简单的 URL 校验
    if not url.startswith("http"):
        await smart_reply_text(update, "❌ 请输入有效的 HTTP/HTTPS 链接。")
        return False

    # 限制每人最多 5 个
    current_subs = await get_user_subscriptions(user_id)
    if len(current_subs) >= 5:
        await smart_reply_text(update, "❌ 订阅数量已达上限 (5个)。请先取消一些订阅。")
        return False
        
    # 尝试解析 RSS 验证有效性
    import feedparser
    # 简单的验证，不阻塞太久
    try:
        msg = await smart_reply_text(update, "🔍 正在验证 RSS 源...")
        # 异步运行 feedparser
        feed = feedparser.parse(url)
        
        # 暂时忽略 bozo，只要有 entries 或 title 就行
             
        title = feed.feed.get("title", url)
        if not title:
             title = url
             
        # 入库
        try:
            await add_subscription(user_id, url, title)
            await smart_edit_text(msg, f"✅ **订阅成功！**\n\n源：{title}\nBot 将每 30 分钟检查一次更新。")
            # 统计
            from stats import increment_stat
            await increment_stat(user_id, "subscriptions_added")
            
            return True
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                await smart_edit_text(msg, "⚠️ 您已经订阅过这个源了。")
                return True # 算作成功
            else:
                 await smart_edit_text(msg, f"❌ 订阅失败: {e}")
                 return False
                 
    except Exception as e:
        logger.error(f"Subscribe error: {e}")
    except Exception as e:
        logger.error(f"Subscribe error: {e}")
        await smart_edit_text(msg, "❌ 无法访问该 RSS 源。")
        return False


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /unsubscribe 命令"""
    if not await check_permission(update):
        return

    # 如果有参数，直接取消该 URL
    # 如果没参数，显示列表按钮（简化起见，让用户复制 URL）
    args = context.args
    if not args:
         await smart_reply_text(update, "⚠️ 用法：`/unsubscribe <RSS链接>`\n请使用 /list_subs 查看您的订阅链接。")
         return
         
    url = args[0]
    user_id = update.effective_user.id
    
    await delete_subscription(user_id, url)
    
    await smart_reply_text(update, f"🗑️ 已取消订阅：`{url}`")


async def monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /monitor 命令，支持交互式输入"""
    if not await check_permission(update):
        return ConversationHandler.END

    args = context.args
    # 如果有参数，直接执行
    if args:
        await process_monitor(update, context, " ".join(args))
        return ConversationHandler.END
        
    # 无参数，提示输入
    # 无参数，提示输入
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


async def handle_monitor_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理监控关键词的输入"""
    keyword = update.message.text
    if not keyword:
        await update.message.reply_text("请发送有效文本。")
        return WAITING_FOR_MONITOR_KEYWORD
        
    success = await process_monitor(update, context, keyword)
    if success:
        return ConversationHandler.END
    else:
        # 如果失败（非重复订阅错误），允许重试
        return WAITING_FOR_MONITOR_KEYWORD


async def process_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE, keyword: str) -> bool:
    """实际处理监控逻辑"""
    user_id = update.effective_user.id
    
    # 限制每人最多 5 个 (与普通订阅共享额度)
    current_subs = await get_user_subscriptions(user_id)
    if len(current_subs) >= 5:
        await smart_reply_text(update, "❌ 订阅数量已达上限 (5个)。请先取消一些订阅。")
        return False

    # 构造 Google News RSS URL
    import urllib.parse
    encoded_keyword = urllib.parse.quote(keyword)
    rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    
    import urllib.parse
    encoded_keyword = urllib.parse.quote(keyword)
    rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    
    msg = await smart_reply_text(update, f"🔍 正在为关键词 '{keyword}' 配置监控...")
    
    try:
        # 验证一下 RSS (虽然 Google News 通常没问题)
        import feedparser
        feed = feedparser.parse(rss_url)
        
        # Google News RSS title通常是 "Google News - keyword"
        title = f"监控: {keyword}"
        
        await add_subscription(user_id, rss_url, title)
        await smart_edit_text(msg,
            f"✅ **监控已设置！**\n\n"
            f"关键词：{keyword}\n"
            f"来源：Google News\n"
            f"Bot 将每 30 分钟推送相关新闻。"
        )
        return True
            
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
             await smart_edit_text(msg, "⚠️ 您已经监控过这个关键词了。")
             return True # 算作成功结束，不再 retry
        else:
             logger.error(f"Monitor error: {e}")
             await smart_edit_text(msg, f"❌ 设置失败: {e}")
             return False


async def list_subs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /list_subs 命令"""
    if not await check_permission(update):
        return

    user_id = update.effective_user.id
    
    subs = await get_user_subscriptions(user_id)
    
    if not subs:
        await smart_reply_text(update, "📭 您当前没有订阅任何 RSS 源。")
        return
        
    msg = "📋 **您的订阅列表**：\n\n"
    for sub in subs:
        title = sub["title"]
        url = sub["feed_url"]
        msg += f"• [{title}]({url})\n  `{url}`\n\n"
        
    msg += "发送 `/unsubscribe <链接>` 可取消订阅。"
    
    await smart_reply_text(update, msg)
