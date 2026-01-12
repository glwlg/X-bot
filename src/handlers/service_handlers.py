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

    user_id = update.effective_user.id
    args = context.args
    
    # 如果有参数，直接取消该 URL
    if args:
        url = args[0]
        await delete_subscription(user_id, url)
        await smart_reply_text(update, f"🗑️ 已取消订阅：`{url}`")
        return
    
    # 无参数：显示订阅列表让用户选择
    subs = await get_user_subscriptions(user_id)
    
    if not subs:
        await smart_reply_text(update, "📭 您当前没有订阅任何内容。")
        return
    
    # 构建按钮列表
    keyboard = []
    for sub in subs:
        title = sub["title"] or sub["feed_url"][:30]
        # 回调数据格式: unsub_<id>
        keyboard.append([InlineKeyboardButton(f"❌ {title}", callback_data=f"unsub_{sub['id']}")])
    
    keyboard.append([InlineKeyboardButton("🚫 取消", callback_data="unsub_cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await smart_reply_text(
        update,
        "📋 **请选择要取消的订阅**：",
        reply_markup=reply_markup
    )


async def handle_unsubscribe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理取消订阅按钮回调"""
    from database import delete_subscription_by_id
    
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id
    
    if data == "unsub_cancel":
        await query.edit_message_text("👌 已取消操作。")
        return
    
    # 解析订阅 ID
    try:
        sub_id = int(data.replace("unsub_", ""))
    except ValueError:
        await query.edit_message_text("❌ 无效的操作。")
        return
    
    # 删除订阅
    success = await delete_subscription_by_id(sub_id, user_id)
    
    if success:
        await query.edit_message_text("✅ 订阅已取消。")
    else:
        await query.edit_message_text("❌ 取消失败，订阅可能已不存在。")


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
    """实际处理监控逻辑，支持多关键词（用顿号、逗号分隔）"""
    import re
    import urllib.parse
    import feedparser
    
    user_id = update.effective_user.id
    
    # 拆分多个关键词（支持顿号、中英文逗号）
    keywords = re.split(r'[、,，]+', keyword.strip())
    keywords = [k.strip() for k in keywords if k.strip()]
    
    if not keywords:
        await smart_reply_text(update, "❌ 请输入有效的关键词。")
        return False
    
    msg = await smart_reply_text(update, f"🔍 正在配置 {len(keywords)} 个关键词监控...")
    
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
    
    # 构建结果消息
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
    
    await smart_edit_text(msg, result_msg)
    return len(success_list) > 0 or len(existed_list) > 0


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


# --- Feature Request ---

FEATURE_STATE_KEY = "feature_request"

async def feature_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理 /feature 命令，收集功能需求"""
    from config import WAITING_FOR_FEATURE_INPUT
    
    if not await check_permission(update):
        return ConversationHandler.END

    # 清除之前的状态
    context.user_data.pop(FEATURE_STATE_KEY, None)
    
    args = context.args
    if args:
        # 有参数，直接处理
        return await process_feature_request(update, context, " ".join(args))
        
    # 无参数，提示输入
    await smart_reply_text(update,
        "💡 **提交功能需求**\n\n"
        "请描述您希望 Bot 拥有的新功能。\n\n"
        "发送 /cancel 取消。"
    )
    return WAITING_FOR_FEATURE_INPUT


async def handle_feature_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理需求的交互式输入（支持多轮补充）"""
    from config import WAITING_FOR_FEATURE_INPUT
    
    text = update.message.text
    if not text:
        await update.message.reply_text("请发送有效文本。")
        return WAITING_FOR_FEATURE_INPUT
    
    # 检查是否已有需求文档
    state = context.user_data.get(FEATURE_STATE_KEY)
    if state and state.get("filepath"):
        # 追加补充信息到已有文档
        return await append_feature_supplement(update, context, text)
    else:
        # 新需求
        return await process_feature_request(update, context, text)


async def save_feature_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """保存需求并结束对话"""
    state = context.user_data.pop(FEATURE_STATE_KEY, None)
    
    if state and state.get("filename"):
        await smart_reply_text(update, f"✅ 需求 `{state['filename']}` 已保存！")
    else:
        await smart_reply_text(update, "✅ 需求收集已结束。")
    
    return ConversationHandler.END


async def process_feature_request(update: Update, context: ContextTypes.DEFAULT_TYPE, description: str) -> int:
    """整理用户需求并保存"""
    import os
    import datetime
    import re
    from config import gemini_client, GEMINI_MODEL, DATA_DIR, WAITING_FOR_FEATURE_INPUT
    
    msg = await smart_reply_text(update, "🤔 正在整理您的需求...")
    
    # 简洁的 prompt
    prompt = f'''用户提出了一个功能需求，请整理成简洁的需求描述。

用户原话：{description}

请按以下格式输出（Markdown），保持简洁：

# [2-6个字的标题]

## 需求描述
1-2 句话描述用户想要什么

## 功能要点
- 要点1
- 要点2（如有）
'''

    try:
        response = await gemini_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        doc_content = response.text.strip()
        
        # 提取标题
        title_match = re.search(r'^#\s*(.+)$', doc_content, re.MULTILINE)
        title = title_match.group(1).strip()[:15] if title_match else "需求"
        title_safe = re.sub(r'[\\/*?:"<>|]', '', title).replace(' ', '_')
        
        # 添加元信息
        timestamp = datetime.datetime.now()
        meta = f"\n\n---\n*提交时间：{timestamp.strftime('%Y-%m-%d %H:%M')} | 用户：{update.effective_user.id}*"
        doc_content += meta
        
        # 保存文件
        feature_dir = os.path.join(DATA_DIR, "feature_requests")
        os.makedirs(feature_dir, exist_ok=True)
        
        date_str = timestamp.strftime("%Y%m%d")
        existing = [f for f in os.listdir(feature_dir) if f.startswith(date_str)]
        seq = len(existing) + 1
        filename = f"{date_str}_{seq:02d}_{title_safe}.md"
        filepath = os.path.join(feature_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(doc_content)
        
        # 保存状态，等待用户补充或确认
        context.user_data[FEATURE_STATE_KEY] = {
            "filepath": filepath,
            "filename": filename,
        }
        
        await smart_edit_text(msg,
            f"📝 **需求已记录**\n\n"
            f"📄 `{filename}`\n\n"
            f"{doc_content}\n\n"
            "---\n继续补充说明，或点击 /save_feature 保存结束。"
        )
        return WAITING_FOR_FEATURE_INPUT
        
    except Exception as e:
        logger.error(f"Feature request error: {e}")
        await smart_edit_text(msg, f"❌ 处理失败：{e}")
        return ConversationHandler.END


async def append_feature_supplement(update: Update, context: ContextTypes.DEFAULT_TYPE, supplement: str) -> int:
    """追加用户补充信息到需求文档"""
    import datetime
    from config import WAITING_FOR_FEATURE_INPUT
    
    state = context.user_data.get(FEATURE_STATE_KEY, {})
    filepath = state.get("filepath")
    filename = state.get("filename")
    
    if not filepath:
        return ConversationHandler.END
    
    msg = await smart_reply_text(update, "📝 正在更新需求...")
    
    try:
        # 读取现有内容
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 追加补充信息
        timestamp = datetime.datetime.now().strftime('%H:%M')
        supplement_section = f"\n\n## 补充说明 ({timestamp})\n{supplement}"
        
        # 插入到元信息之前
        if "---\n*提交时间" in content:
            parts = content.rsplit("---\n*提交时间", 1)
            content = parts[0].rstrip() + supplement_section + "\n\n---\n*提交时间" + parts[1]
        else:
            content += supplement_section
        
        # 保存
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        await smart_edit_text(msg,
            f"✅ **补充已添加**\n\n"
            f"📄 `{filename}`\n\n"
            "继续补充说明，或点击 /save_feature 保存结束。"
        )
        return WAITING_FOR_FEATURE_INPUT
        
    except Exception as e:
        logger.error(f"Append feature error: {e}")
        await smart_edit_text(msg, f"❌ 更新失败：{e}")
        return ConversationHandler.END

